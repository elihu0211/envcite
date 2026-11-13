"""抽取器：由語言模型從條文單元寫出標註，狀態為 extracted。

模型不負責錨點：每個陳述附 `quote`，span、雜湊、`text` 由這裡在原文定位；引文不在原文的陳述丟掉。
模型常把原文的字寫成異體字（污→汙），定位時兩邊一併歸一，span 仍取自原文。
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Any, NamedTuple, cast

from envcite_ontology.claims import (
    LEGAL_EFFECTS,
    LIABILITY_BASES,
    TRIGGER_KINDS,
    AnnotationError,
    Loaded,
    load,
)
from envcite_ontology.db import Conn, fetch_one
from envcite_ontology.verification import prompts
from envcite_ontology.verification.carry import carry, truncated_issues
from envcite_ontology.verification.context import ancestors_text
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError
from envcite_ontology.verification.subject import normalize as normalize_subjects
from envcite_ontology.violation import citations

MAX_REPAIRS = 2


def _nullable(inner: dict[str, Any]) -> dict[str, Any]:
    """模型常把選填欄位照樣板填成空值：Schema 容許 null，由後面的整形與驗證處理。"""
    return {"anyOf": [inner, {"type": "null"}]}


_STR: dict[str, Any] = {"type": "string"}
_NUM: dict[str, Any] = {"type": "number"}
_BOOL: dict[str, Any] = {"type": "boolean"}
_SLOT_VALUE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "literal": _STR,
        "liability": {"enum": [*LIABILITY_BASES, None]},
        "unstated": _BOOL,
    },
}
_SLOT: dict[str, Any] = {
    "anyOf": [_SLOT_VALUE, {"type": "array", "items": _SLOT_VALUE}, _STR],
}

#: 解碼期約束只管形狀：結構、型別與列舉值。引文逐字在原文、概念已宣告、duty 與 scope 完備等
#: 語意性質不在此，仍由事後檢查負責（見 01-12 §4）。
STATEMENTS_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "statements",
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"code": _STR, "label": _STR},
                        "required": ["code", "label"],
                    },
                },
                "statements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": _STR,
                            "quote": _STR,
                            "kind": {"enum": ["prescriptive", "constitutive", "sanction"]},
                            "deontic": {"enum": ["obligation", "prohibition", "permission", None]},
                            "slots": {"type": "object", "additionalProperties": _SLOT},
                            "conditions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"enum": ["scope", "duty", "limit"]},
                                        "clause": {"type": "integer"},
                                        "concept": _STR,
                                        "comparator": {
                                            "enum": ["le", "lt", "ge", "gt", "eq", "ne"]
                                        },
                                        "threshold": _nullable(_NUM),
                                        "unit": _nullable(_STR),
                                        "negated": _BOOL,
                                        "judgement": _BOOL,
                                        "value_concept": _nullable(_STR),
                                        "permit": _nullable(_STR),
                                        "method": _nullable(_STR),
                                        "averaging": _nullable(_STR),
                                    },
                                },
                            },
                            "sanction": _nullable(
                                {
                                    "type": "object",
                                    "properties": {
                                        "effect": {"enum": [*LEGAL_EFFECTS, None]},
                                        "min": _nullable(_NUM),
                                        "max": _nullable(_NUM),
                                        "amount_text": _nullable(_STR),
                                        "repeating": _BOOL,
                                        "discretionary": _BOOL,
                                        "punishes": _nullable(_STR),
                                        "triggers": _nullable(
                                            {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "statement": _STR,
                                                        "kind": {"enum": list(TRIGGER_KINDS)},
                                                    },
                                                    "required": ["statement", "kind"],
                                                },
                                            }
                                        ),
                                    },
                                }
                            ),
                        },
                        "required": ["id", "quote", "kind"],
                    },
                },
            },
            "required": ["statements"],
        },
    },
}


def _complete(model: Model, prompt: str) -> str:
    """送出一次抽取呼叫；模型宣告要解碼期約束時才帶 Schema，其餘與原本相同。"""
    if getattr(model, "constrained_extraction", False):
        complete = cast(Any, model).complete
        return str(complete(prompt, response_format=STATEMENTS_FORMAT))
    return model.complete(prompt)


#: 異體字一對一歸一（位置不變）：全國法規資料庫寫「污」「巿」，模型常寫成「汙」「市」。
_VARIANTS = str.maketrans({"汙": "污", "巿": "市"})


def locate(quote: str, text: str) -> int:
    """引文在原文的起點，異體字視為同字；不在原文（或空引文）為 -1。"""
    return text.translate(_VARIANTS).find(quote.translate(_VARIANTS)) if quote else -1


_CONDITIONAL = re.compile(r"者[，,]\s*(應|不得)")


#: 規範性陳述的助動詞：引文不含它，就是從規範句切下的子句（主體、適用情形或義務方向在別處）。
_MODAL = re.compile(r"應|不得|須|得|禁止|始可")


def fragments(statements: Sequence[Mapping[str, Any]], text: str) -> dict[str, str]:
    """規範性陳述的引文缺助動詞而單元原文有（id → 引文）：引文是從規範句切下的子句（提示規則 14）。

    款、目常只列行為，助動詞在上層條文（「不得有下列行為：」），原文本身沒有助動詞時不判。
    """
    if not _MODAL.search(text):
        return {}
    return {
        str(s.get("id", "?")): str(s.get("quote", ""))
        for s in statements
        if s.get("kind") == "prescriptive" and not _MODAL.search(str(s.get("quote", "")))
    }


def fragment_issues(statements: Sequence[Mapping[str, Any]], text: str) -> list[str]:
    """`fragments` 的修復訊息。修不好就丟：缺了主體、適用情形或方向的子句，評不出忠於原文。"""
    return [
        f"陳述 {ident} 的引文「{quote}」不含「應」「不得」「得」等助動詞："
        "依規則 14，引文從主體與適用情形起，含助動詞，到這個義務的最後一個行為；同一個「應」之下"
        "並列的行為寫成同一個陳述的幾個 duty 要件。"
        for ident, quote in fragments(statements, text).items()
    ]


def completeness_issues(statements: Sequence[Mapping[str, Any]]) -> list[str]:
    """義務要有 duty 要件、「…者，應…」要有 scope 要件；只查結構有沒有寫，不查對錯。"""
    issues: list[str] = []
    for s in statements:
        for c in s.get("conditions") or []:
            if (
                isinstance(c, Mapping)
                and c.get("value_concept")
                and c.get("comparator") not in ("eq", "ne")
            ):
                issues.append(
                    f"陳述 {s.get('id', '?')} 的要件以 value_concept 比較，"
                    "comparator 只能是 eq 或 ne（屬於或不屬於）。"
                )
        if s.get("kind") == "prescriptive" and not s.get("deontic"):
            issues.append(
                f"陳述 {s.get('id', '?')} 是 prescriptive 卻沒有 deontic（義務、禁止或許可）。"
            )
        if s.get("kind") != "prescriptive" or s.get("deontic") not in ("obligation", "prohibition"):
            continue
        roles = {c.get("role") for c in s.get("conditions") or [] if isinstance(c, Mapping)}
        ident = s.get("id", "?")
        if "duty" not in roles:
            issues.append(
                f"陳述 {ident} 是義務或禁止卻沒有 duty 要件：「應」「不得」之後的內容要寫成 duty。"
            )
        if _CONDITIONAL.search(str(s.get("quote", ""))) and "scope" not in roles:
            issues.append(
                f"陳述 {ident} 是「…者，應…」句型卻沒有 scope 要件：「者」之前的條件要寫成 scope。"
            )
    return issues


#: 一個罰則句常把幾個效果接在一起；每個效果的子句有這些字樣。罰則的引文只取自己效果的子句。
_EFFECT_CLAUSES = {
    "fine": re.compile(r"罰鍰"),
    "order": re.compile(
        r"通知[^，；。]{0,8}(限期|禁止|停止|補正|改善)|命[^，；。]{0,6}限期|限期(改善|補正|拆除)"
    ),
    "repeating": re.compile(r"按(次|日)(連續)?處罰"),
    "restriction": re.compile(r"停工|停業|禁止供飲用"),
}
#: 刑罰的字樣：它不是行政罰，沒有對應的 effect（罰金不是 fine）。
_CRIMINAL = re.compile(r"有期徒刑|拘役|罰金")


def _own_clauses(sanction: Mapping[str, Any]) -> set[str]:
    effect = str(sanction.get("effect"))
    own = {effect} if effect in _EFFECT_CLAUSES else set()
    if sanction.get("repeating"):
        own.add("repeating")
    return own


def sanction_issues(statements: Sequence[Mapping[str, Any]]) -> list[str]:
    """罰則的引文含別的效果的子句（約定 B、C）：一個效果一個陳述，引文只取自己的子句。

    只請模型修，修不好不丟，交給驗證：併在一起的陳述不是錯的，只是遺漏了其餘的效果。
    """
    issues: list[str] = []
    for s in statements:
        sanction = s.get("sanction")
        if s.get("kind") != "sanction" or not isinstance(sanction, Mapping):
            continue
        quote = str(s.get("quote", ""))
        own = _own_clauses(sanction)
        extra = sorted(name for name, clause in _EFFECT_CLAUSES.items() if clause.search(quote))
        extra = [name for name in extra if name not in own]
        if own and extra:
            issues.append(
                f"陳述 {s.get('id', '?')} 的引文含另一個效果的子句（{'、'.join(extra)}）："
                "依約定 B、C，一個效果一個陳述，引文只取自己效果的子句（其餘的效果各寫成自己的"
                "sanction 陳述，沒有寫就補上）。"
            )
    return issues


def criminal_penalties(
    statements: Sequence[Mapping[str, Any]], text: str, context: str
) -> dict[str, str]:
    """把刑罰（徒刑、拘役、罰金）當成罰鍰的罰則（id → 原因）：行政罰以外沒有對應的 effect。

    款、目只有半句話，處罰寫在上層條文，故連上層一起看；上層與本文同時出現罰鍰時不判。
    """
    whole = f"{context}\n{text}"
    criminal_context = "罰鍰" not in whole and bool(_CRIMINAL.search(whole))
    out: dict[str, str] = {}
    for s in statements:
        sanction = s.get("sanction")
        if s.get("kind") != "sanction" or not isinstance(sanction, Mapping):
            continue
        if sanction.get("effect") != "fine":
            continue
        if _CRIMINAL.search(str(sanction.get("amount_text") or "")) or criminal_context:
            out[str(s.get("id", "?"))] = "criminal penalty is not an administrative sanction"
    return out


def _statements_of(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """模型回答裡的陳述；形狀不對的項目當作沒有（外來輸出不可信）。"""
    items = answer.get("statements")
    return [x for x in items if isinstance(x, Mapping)] if isinstance(items, list) else []


def _concepts_of(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """宣告完整（有 code 與 label）的概念；別的項目當作沒宣告。

    只留提示要的欄位：模型自己多寫的鍵（例如 broader）不可信，載入器會因它拒絕整份文件。
    """
    items = answer.get("concepts")
    return [
        {"code": c["code"], "label": c["label"]}
        for c in (items if isinstance(items, list) else [])
        if isinstance(c, Mapping) and c.get("code") and c.get("label")
    ]


def _literal(value: Any) -> Any:
    """槽位值裡的字串是字面；清單逐項處理。"""
    if isinstance(value, str):
        return {"literal": value}
    if isinstance(value, list):
        return [_literal(v) for v in value]
    return value


def _liability(role: str, value: Any) -> Any:
    """責任基礎只屬主體、只取列舉值；別處或別的值當作沒寫（載入器會因它拒絕整份文件）。

    「未明示」同樣只屬主體；別的槽位寫它等於沒有值，整個槽位值去掉。
    """
    if isinstance(value, list):
        kept = [_liability(role, v) for v in value]
        return [v for v in kept if v != {}]
    if isinstance(value, Mapping):
        if "liability" in value and (
            role != "subject" or value["liability"] not in LIABILITY_BASES
        ):
            value = {k: v for k, v in value.items() if k != "liability"}
        if role != "subject" and "unstated" in value:
            value = {k: v for k, v in value.items() if k != "unstated"}
    return value


def _tidy_condition(c: Mapping[str, Any]) -> dict[str, Any]:
    """一個要件的形狀修正（見 `tidy`）。承自上位是程式標的（`carry`），模型寫的不算。"""
    drop: set[str] = {"carried"}
    if c.get("value_concept") or c.get("permit"):
        drop.add("threshold")
    if c.get("role") == "limit":
        drop.add("judgement")
    else:
        drop |= {"method", "averaging"}
    return {k: v for k, v in c.items() if k not in drop}


def tidy(answer: Mapping[str, Any], defined: Mapping[str, str] | None = None) -> dict[str, Any]:
    """不改變意思的形狀修正。

    槽位直接寫成字串（`"subject": "甲"`，或清單裡的字串）就是它的字面；與概念值比較或委由
    個別處分的要件沒有門檻值，模型多寫的 threshold 去掉；主體以外或不在列舉裡的責任基礎去掉；
    認定方式只屬限值、限值不標需判斷，別處的去掉（載入器會因它拒絕整份文件）；
    要件指名的本法用語代碼，補進宣告的概念（名稱取自該法的定義）。觸發不在這裡修：少了前提的
    罰則會變成無條件，由 `_local_triggers` 整個丟掉。
    """
    out = dict(answer)
    out["statements"] = [
        {
            **s,
            "slots": {k: _liability(str(k), _literal(v)) for k, v in slots.items()},
        }
        if isinstance(slots := s.get("slots"), Mapping)
        else dict(s)
        for s in _statements_of(answer)
    ]
    for s in out["statements"]:
        conditions = s.get("conditions")
        if not isinstance(conditions, list):
            continue
        s["conditions"] = [_tidy_condition(c) if isinstance(c, Mapping) else c for c in conditions]
    if defined:
        declared = {str(c["code"]) for c in _concepts_of(answer)}
        used = {
            str(c["value_concept"])
            for s in out["statements"]
            for c in s.get("conditions") or []
            if isinstance(c, Mapping) and str(c.get("value_concept")) in defined
        }
        out["concepts"] = [
            *_concepts_of(answer),
            *({"code": code, "label": defined[code]} for code in sorted(used - declared)),
        ]
    return out


def shape_issues(
    answer: Mapping[str, Any], defined: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """載入器必然拒絕的形狀（id → 問題）：要件非物件、無 concept、concept 未宣告、
    概念值要件比大小，或罰則陳述的種類不是 sanction。

    載入器一遇到就整份不寫、連帶丟掉正確的陳述；故先請模型修，修不好才只丟該陳述。
    """
    declared = {str(c["code"]) for c in _concepts_of(answer)} | set(defined or {})
    out: dict[str, list[str]] = {}
    for s in _statements_of(answer):
        problems: list[str] = []
        for c in s.get("conditions") or []:
            if not isinstance(c, Mapping):
                problems.append("要件必須是物件（role、concept、comparator、threshold…）。")
                continue
            for field in ("concept", "value_concept", "permit", "method", "averaging"):
                code = c.get(field)
                if field == "concept" and not code:
                    problems.append(
                        f"有一個要件沒有 concept（role={c.get('role')}）：每個要件都要指名一個"
                        "在 concepts 裡宣告過的代碼。"
                    )
                elif field == "value_concept" and code and str(code) not in (defined or {}):
                    problems.append(
                        f"要件的 value_concept「{code}」不在本法已定義的用語清單裡；"
                        "清單沒有的不要寫 value_concept。"
                    )
                elif code and str(code) not in declared:
                    problems.append(
                        f"要件的 {field}「{code}」沒有在 concepts 裡宣告（code 與 label 都要寫）。"
                    )
            if c.get("role") == "limit" and c.get("threshold") is None:
                problems.append(f"限值「{c.get('concept')}」沒有 threshold：限值要寫原文的數值。")
            if c.get("value_concept") and c.get("permit"):
                problems.append(
                    f"要件「{c.get('concept')}」同時寫了 value_concept 與 permit："
                    "門檻值只能有一種來源。"
                )
            if c.get("value_concept") and c.get("comparator") not in ("eq", "ne"):
                problems.append(
                    f"要件的 value_concept「{c.get('value_concept')}」只能用 eq 或 ne："
                    "用語只有屬於與否，沒有大小。"
                )
        # 只看填了效果的罰則：照樣板填成空值的會被忽略；處罰別的罰則者留待連結，不在這裡判
        sanction = s.get("sanction")
        if isinstance(sanction, Mapping) and sanction.get("effect") and s.get("kind") != "sanction":
            problems.append("帶 sanction 的陳述 kind 必須是 sanction。")
        if problems:
            out[str(s.get("id", "?"))] = problems
    return out


def borrowed_issues(
    statements: Sequence[Mapping[str, Any]], defined: Mapping[str, str] | None
) -> list[str]:
    """義務內容或適用範圍借用本法已定義的泛稱（約定 G）：只請模型修，修不好不丟，交給驗證。

    用語只屬 value_concept；以它當 duty 的概念，標註說的不是引文的那件事。
    """
    if not defined:
        return []
    return [
        f"陳述 {s.get('id', '?')} 的 {c.get('role')} 要件借用本法已定義的用語「{c['concept']}」"
        "當概念：請依約定 G 另外宣告，用引文自己的字命名這句話說的那件事"
        "（用語只能放 value_concept）。"
        for s in statements
        if s.get("kind") == "prescriptive"
        for c in s.get("conditions") or []
        if isinstance(c, Mapping)
        and c.get("role") in ("duty", "scope")
        and str(c.get("concept")) in defined
        and not c.get("value_concept")
    ]


#: 不必寫進要件的字：標點、連接詞與助詞。
_FILLER = re.compile(r"[，、。；：「」（）()\s]|並|及|或|與|之|其|者|時|於|以|為|應|不得")
#: 要件標籤與原文的共同字串至少這麼長才算涵蓋（單字幾乎處處相同）。
_MIN_SHARED = 2
#: 漏掉的片段去掉連接詞後至少這麼長才回報。
_MIN_GAP = 3


def _gaps(span: str, labels: Sequence[str]) -> list[str]:
    """原文片段中沒被任何標籤逐字涵蓋的部分（依序），去掉連接詞後太短的不算。"""
    span = span.translate(_VARIANTS)
    covered = [False] * len(span)
    for label in (x.translate(_VARIANTS) for x in labels):
        for i in range(len(span)):
            n = _MIN_SHARED
            while i + n <= len(span) and span[i : i + n] in label:
                n += 1
            if n > _MIN_SHARED:
                covered[i : i + n - 1] = [True] * (n - 1)
    pieces, at = [], 0
    for hit, run in groupby(covered):
        width = len(list(run))
        if not hit:
            pieces.append(span[at : at + width].strip("，、。；： "))
        at += width
    return [p for p in pieces if len(_FILLER.sub("", p)) >= _MIN_GAP]


def _subjects(s: Mapping[str, Any]) -> list[str]:
    slots = s.get("slots")
    value = slots.get("subject") if isinstance(slots, Mapping) else None
    values = value if isinstance(value, list) else [value]
    return [str(v.get("literal", "")) for v in values if isinstance(v, Mapping)]


def uncovered_issues(
    statements: Sequence[Mapping[str, Any]], labels: Mapping[str, str]
) -> list[str]:
    """義務的要件沒涵蓋引文（約定 G）：「應」之後的字要在 duty，之前的要在 scope 或主體。

    `labels` 是概念代碼到標籤。駁回的義務多半漏了期限、對象、方式或主體的限定語，確認的幾乎全涵蓋；
    只請模型修，修不好不丟，交給驗證。
    """
    issues: list[str] = []
    for s in statements:
        if s.get("kind") != "prescriptive" or s.get("deontic") not in ("obligation", "prohibition"):
            continue
        quote = str(s.get("quote", ""))
        modal = _MODAL.search(quote)
        if modal is None:
            continue
        named: dict[str, list[str]] = {"duty": [], "scope": _subjects(s)}
        for c in s.get("conditions") or []:
            if isinstance(c, Mapping) and c.get("role") in named:
                named[str(c["role"])].append(labels.get(str(c.get("concept")), ""))
        ident = s.get("id", "?")
        if gaps := _gaps(quote[modal.end() :], named["duty"]):
            issues.append(
                f"陳述 {ident} 的 duty 要件沒有涵蓋引文的「{'」「'.join(gaps)}」：義務的每個行為"
                "與它的期限、對象、方式都是義務內容，依約定 G 用引文的字寫進 duty 概念的標籤"
                "（一個行為一個 duty）。"
            )
        if gaps := _gaps(quote[: modal.start()], named["scope"]):
            issues.append(
                f"陳述 {ident} 的 scope 要件與主體沒有涵蓋引文的「{'」「'.join(gaps)}」："
                "「應」之前的適用情形與主體的限定語（經…公告之、設置…之）各寫成一個 scope 要件，"
                "用引文的字命名。"
            )
    return issues


def unusable(statements: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """修復後仍不可用的陳述（id → 原因）：規範性陳述無方向，或義務、禁止無 duty 要件。

    `executable` 閘門只管義務，無 duty 的禁止會只靠模型閘門通過，故不寫入。
    無 scope 的義務可評估（對任何人適用），留給獨立測試。
    """
    out: dict[str, str] = {}
    for s in statements:
        if s.get("kind") != "prescriptive":
            continue
        roles = {c.get("role") for c in s.get("conditions") or [] if isinstance(c, Mapping)}
        if not s.get("deontic"):
            out[str(s.get("id", "?"))] = "prescriptive without a direction"
        elif s["deontic"] in ("obligation", "prohibition") and "duty" not in roles:
            out[str(s.get("id", "?"))] = "obligation or prohibition without a duty condition"
    return out


@dataclass(frozen=True)
class Counts:
    """一個單元的抽取過程計數，供比較不同抽取路徑（`verification` 的 A/B 量測）。"""

    #: 呼叫模型的次數（首次加修復）
    calls: int = 0
    #: 用掉的修復次數（0 至 `MAX_REPAIRS`）
    repairs: int = 0
    #: 語法失敗：首次輸出取不出 JSON（0 或 1）
    syntax_failures: int = 0
    #: 形狀問題：首次輸出被 `shape_issues` 指出的問題數（概念未宣告等，多為語意，不是語法）
    shape_failures: int = 0


@dataclass(frozen=True)
class Extracted:
    """一個單元的陳述抽取結果；`loaded` 為 None 表示什麼都沒載入，原因在 `dropped`。"""

    loaded: Loaded | None
    #: 丟掉的輸出：引文不在原文的陳述，或違反資料庫約束的整份文件
    dropped: tuple[str, ...]
    counts: Counts = Counts()


class _UnitTextRow(NamedTuple):
    provision_id: int
    content: str


_UNIT_TEXT = (
    "SELECT p.id, v.content FROM corpus.provision AS p"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
    "   AND v.content IS NOT NULL ORDER BY v.id DESC LIMIT 1"
)


def _unit_text(conn: Conn, law: str, path: str) -> tuple[int, str]:
    """單元的 provision id 與現行文字。"""
    row = fetch_one(conn, _UNIT_TEXT, (law, path), _UnitTextRow)
    if row is None:
        raise LookupError(f"no current text for {law} {path}")
    return row.provision_id, row.content


def _cites_elsewhere(path: str, text: str) -> bool:
    """原文引用別條而未引用自己：處罰的是被引用的條，不是單元內其他陳述。"""
    cited = citations(text)
    return bool(cited) and not any(path == c or path.startswith(c + "/") for c in cited)


def _aimed(s: dict[str, Any], duties: set[str], elsewhere: bool) -> dict[str, Any]:
    """罰則處罰的若不是同單元的規範性陳述，或原文引用了別條，就不指明受罰者：處罰的是被引條文的義務，
    由依賴圖找（`annotation.violation`），載入時存成待連結罰則。

    模型會把罰則連到同單元重述義務的陳述（「違反第三十八條第三項規定，…者，處…」）；
    引用閘門雖會擋下，卻白丟一個連得上的罰則，故原文引別條一律交給連結。
    """
    sanction = s.get("sanction")
    if not isinstance(sanction, Mapping) or (
        str(sanction.get("punishes")) in duties and not elsewhere
    ):
        return s
    return {**s, "sanction": {k: v for k, v in sanction.items() if k != "punishes"}}


def _trigger_problem(
    ident: str, trigger: Any, effects: Mapping[str, Any]
) -> tuple[str | None, dict[str, str] | None]:
    """一個觸發的問題（沒有則 None），與整形後的觸發。"""
    if not isinstance(trigger, Mapping) or trigger.get("kind") not in TRIGGER_KINDS:
        return "a trigger is malformed", None
    target = str(trigger.get("statement"))
    if target == ident or target not in effects:
        return f"trigger {target} is not in this unit", None
    if trigger["kind"] == "order_lapsed" and effects[target] != "order":
        return f"trigger {target} is not an order", None
    return None, {"statement": target, "kind": str(trigger["kind"])}


def _local_triggers(statements: list[dict[str, Any]], dropped: list[str]) -> list[dict[str, Any]]:
    """觸發只連到同一份文件裡的罰則，命令屆期未履行的前提須是命令。有一個觸發不對就丟掉整個罰則：
    少了前提的罰則會被當成無條件。連帶丟掉以被丟者為前提或為例外者，直到不再變動。"""
    while True:
        effects = {
            str(s.get("id")): s["sanction"].get("effect")
            for s in statements
            if isinstance(s.get("sanction"), Mapping)
        }
        ids = {str(s.get("id")) for s in statements}
        kept: list[dict[str, Any]] = []
        for s in statements:
            ident = str(s.get("id"))
            if s.get("exception_of") is not None and str(s["exception_of"]) not in ids:
                dropped.append(f"{ident}: excepts nothing in this unit")
                continue
            sanction = s.get("sanction")
            if not isinstance(sanction, Mapping) or not sanction.get("triggers"):
                kept.append(s)
                continue
            triggers = sanction["triggers"]
            checked = [
                _trigger_problem(ident, t, effects)
                for t in (triggers if isinstance(triggers, list) else [None])
            ]
            problem = next((p for p, _ in checked if p is not None), None)
            if problem is not None:
                dropped.append(f"{ident}: {problem}")
                continue
            kept.append({**s, "sanction": {**sanction, "triggers": [t for _, t in checked]}})
        if len(kept) == len(statements):
            return kept
        statements = kept


def penalized_issues(statements: Sequence[Mapping[str, Any]], by: Sequence[str]) -> list[str]:
    """被處罰條文以「違反」引用的單元，卻沒有任何義務或禁止陳述：送回修復，修不好不丟。"""
    if not by or any(
        s.get("kind") == "prescriptive" and s.get("deontic") in ("obligation", "prohibition")
        for s in statements
    ):
        return []
    return [
        f"本單元被處罰條文（{'、'.join(by)}）以「違反」引用，卻沒有任何 obligation 或 prohibition"
        "的陳述：找出它課予的義務或禁止，「始得」之前的「應」是義務，不是許可。"
    ]


def _located(answer: Mapping[str, Any], text: str) -> list[Mapping[str, Any]]:
    """引文在原文裡的陳述；引文不在的，後面一律丟掉，不值得請模型修。"""
    return [x for x in _statements_of(answer) if locate(str(x.get("quote", "")), text) >= 0]


def _repair_issues(
    answer: Mapping[str, Any],
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
    punished_by: Sequence[str],
) -> list[str]:
    """該請模型修的問題；空表示這一版可以收。"""
    here = _located(answer, text)
    shape = shape_issues(answer, defined)
    return (
        completeness_issues(here)
        + fragment_issues(here, text)
        + borrowed_issues(here, defined)
        + uncovered_issues(
            here,
            {str(c["code"]): str(c["label"]) for c in _concepts_of(answer)} | dict(defined or {}),
        )
        + truncated_issues(here, text)
        + sanction_issues(here)
        + penalized_issues(here, punished_by)
        + [
            f"陳述 {ident}：{reason}。刑罰（有期徒刑、拘役、罰金）不是行政罰，沒有對應的 effect"
            "（罰金不是 fine）：這樣的罰則不要寫成 sanction 陳述。"
            for ident, reason in criminal_penalties(here, text, context).items()
        ]
        + [
            f"陳述 {ident}：{problem}"
            for x in here
            for ident in [str(x.get("id", "?"))]
            for problem in shape.get(ident, [])
        ]
    )


def _repair(
    model: Model,
    prompt: str,
    raw: str,
    answer: Mapping[str, Any] | None,
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
    punished_by: Sequence[str],
) -> tuple[Mapping[str, Any] | None, int]:
    """有問題就請模型修，最多 `MAX_REPAIRS` 輪；回傳最後採用的回答與修復的輪數。

    修復的呼叫失敗或回不出物件就沿用現有版本，缺的由後面的丟棄與驗證處理。
    """
    repairs = 0
    for _ in range(MAX_REPAIRS):
        if answer is None:
            break
        issues = _repair_issues(answer, text, context, defined, punished_by)
        if not issues:
            break
        repairs += 1
        try:
            reply = _complete(model, prompts.repair(prompt=prompt, previous=raw, issues=issues))
        except ModelError:
            break
        again = object_from(reply)
        if again is None:
            break
        raw, answer = json.dumps(again, ensure_ascii=False), tidy(again, defined)
    return answer, repairs


def _supplemented(
    answer: Mapping[str, Any], text: str, context: str, defined: Mapping[str, str] | None
) -> dict[str, Any]:
    """主體與適用情形由程式補上，不靠抽取者記得。"""
    # 主體只留原文寫出的行為人；其餘記成未明示（不靠提示：見 `subject`）
    answer = {**answer, "statements": normalize_subjects(_statements_of(answer), text, context)[0]}
    # 上位項與句首的適用情形由程式補上，不靠抽取者記得（見 `carry`）
    labels = {str(c["code"]): str(c["label"]) for c in _concepts_of(answer)} | dict(defined or {})
    carried, new_concepts = carry(_statements_of(answer), text, context, labels)
    return {**answer, "statements": carried, "concepts": [*_concepts_of(answer), *new_concepts]}


def _broken_ids(
    answer: Mapping[str, Any], text: str, context: str, defined: Mapping[str, str] | None
) -> dict[str, str]:
    """修復之後仍不能載入的陳述（id → 原因）。"""
    located = _located(answer, text)
    broken = unusable(located)
    broken |= dict.fromkeys(fragments(located, text), "prescriptive quote without a modal")
    broken |= criminal_penalties(located, text, context)
    for ident in shape_issues(answer, defined):
        broken.setdefault(ident, "malformed conditions")
    return broken


def _unit_statement(
    s: Mapping[str, Any],
    broken: Mapping[str, str],
    law: str,
    path: str,
    text: str,
    dropped: list[str],
) -> dict[str, Any] | None:
    """一個陳述整成要載入的樣子，並錨定在原文；要丟的回 None，原因記進 `dropped`。"""
    ident = s.get("id", "?")
    quote = str(s.get("quote", ""))
    start = locate(quote, text)
    if start < 0:
        dropped.append(f"{ident}: quote is not in the text")
        return None
    if str(ident) in broken:
        dropped.append(f"{ident}: {broken[str(ident)]} after repair")
        return None
    item = {k: v for k, v in s.items() if k != "quote"}
    if item.get("kind") != "prescriptive":
        item.pop("deontic", None)  # 只有規範性陳述有義務方向（提示規則 2）
    sanction = item.get("sanction")
    if not (isinstance(sanction, Mapping) and sanction.get("effect")):
        # 模型常把罰則欄位照樣板填成空值：沒有效果就不是罰則
        item.pop("sanction", None)
        if item.get("kind") == "sanction":
            dropped.append(f"{ident}: penalty without a sanction effect")
            return None
    elif item.get("kind") != "sanction":
        del item["sanction"]  # 罰則只屬於 sanction
    item |= {"law": law, "path": path, "text": text}
    if len(quote) != len(text):
        item["span"] = [start, start + len(quote)]
    return item


def _unit_statements(
    answer: Mapping[str, Any],
    law: str,
    path: str,
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """可載入的陳述，與被丟掉的陳述及原因。"""
    broken = _broken_ids(answer, text, context, defined)
    statements: list[dict[str, Any]] = []
    dropped: list[str] = []
    for s in _statements_of(answer):
        item = _unit_statement(s, broken, law, path, text, dropped)
        if item is not None:
            statements.append(item)
    return statements, dropped


def _settled_sanctions(
    statements: list[dict[str, Any]], path: str, text: str, dropped: list[str]
) -> list[dict[str, Any]]:
    """罰則的受罰者與觸發整理成載入器接受的樣子；丟掉的記進 `dropped`。"""
    duties = {str(s.get("id")) for s in statements if s.get("kind") == "prescriptive"}
    elsewhere = _cites_elsewhere(path, text)
    return _local_triggers([_aimed(s, duties, elsewhere) for s in statements], dropped)


def extract_unit(
    conn: Conn,
    model: Model,
    law: str,
    path: str,
    *,
    agent: str | None = None,
    agent_version: str,
    scheme: tuple[str, str] = ("env", "環境法概念"),
    defined: Mapping[str, str] | None = None,
    punished_by: Sequence[str] = (),
    review: Sequence[str] = (),
) -> Extracted:
    """抽取一個條文單元。`agent` 記為推導者（預設為模型的名字）；驗證者不得與它相同。

    `punished_by` 是以「違反」引用本單元的處罰條文（`link.penalized`）；
    `review` 是重抽時審查者對上一輪的意見。
    """
    provision_id, text = _unit_text(conn, law, path)
    context = ancestors_text(conn, provision_id)
    prompt = prompts.extract(
        law=law,
        path=path,
        text=text,
        context=context,
        defined=defined,
        punished_by=punished_by,
        review=review,
    )
    try:
        raw = _complete(model, prompt)
    except ModelError as e:
        # 重試用盡：不寫入，下次仍是「還沒抽」
        return Extracted(None, (f"<model error: {e}>"[:300],), Counts(calls=1))
    answer: Mapping[str, Any] | None = object_from(raw)
    answer = None if answer is None else tidy(answer, defined)
    syntax_failures = 1 if answer is None else 0
    shape_failures = (
        0 if answer is None else sum(len(v) for v in shape_issues(answer, defined).values())
    )
    answer, repairs = _repair(model, prompt, raw, answer, text, context, defined, punished_by)
    counts = Counts(
        calls=1 + repairs,
        repairs=repairs,
        syntax_failures=syntax_failures,
        shape_failures=shape_failures,
    )
    if answer is None:
        return Extracted(None, ("<unparseable model output>",), counts)
    answer = _supplemented(answer, text, context, defined)
    statements, dropped = _unit_statements(answer, law, path, text, context, defined)
    statements = _settled_sanctions(statements, path, text, dropped)
    base = {
        "derivation": {
            "method": "llm",
            "agent": agent or model.name,
            "agent_version": agent_version,
        },
        "review": "extracted",
        "scheme": {"code": scheme[0], "title": scheme[1]},
        "concepts": _concepts_of(answer),
    }
    if not statements:
        return Extracted(None, tuple(dropped), counts)
    doc = base | {"statements": statements}
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        # 違反約束的文件整份不寫（`load` 已回滾），記下來，不拖垮管線
        return Extracted(None, (*dropped, f"<invalid document: {e}>"), counts)
    return Extracted(loaded, tuple(dropped), counts)
