"""抽取器：由語言模型從條文單元寫出標註，狀態為 extracted。

模型不負責錨點：每個陳述附 `quote`，span、雜湊、`text` 由這裡在原文定位；引文不在原文的陳述丟掉。
模型常把原文的字寫成異體字（污→汙），定位時兩邊一併歸一，span 仍取自原文。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from envcite_ontology.annotate import (
    LEGAL_EFFECTS,
    LIABILITY_BASES,
    TRIGGER_KINDS,
    AnnotationError,
    Loaded,
    load,
)
from envcite_ontology.verification.context import ancestors_text, context_block
from envcite_ontology.verification.conventions import apply as with_conventions
from envcite_ontology.verification.gates import citations
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError

Conn = psycopg.Connection[tuple[object, ...]]

PROMPT = (
    with_conventions("""你是法規標註器。下面是一個條文單元的原文。
請只依原文，輸出一個 JSON 物件：
{{"concepts": [{{"code": "英文小寫底線的代碼", "label": "中文名稱"}}],
  "statements": [{{"id": "短識別", "quote": "原文中的一段，逐字複製", "kind":
  "prescriptive|constitutive|sanction", "deontic": "obligation|prohibition|permission 或省略",
  "slots": {{"subject|action|object|medium": {{"literal": "…"}} 或 [{{"literal": "…"}}, …]}},
  "conditions": [{{"role": "scope|duty|limit", "clause": 1, "concept": "代碼", "comparator":
  "le|lt|ge|gt|eq|ne", "threshold": 數字, "unit": "單位或省略", "negated": false,
  "judgement": false, "value_concept": "本法用語的代碼（見下方清單），沒有就省略",
  "permit": "許可種類的代碼（門檻值是個別許可所載的量值時），沒有就省略",
  "method": "檢測方法的代碼（只有 limit），沒有就省略",
  "averaging": "平均時段的代碼（只有 limit），沒有就省略"}}],
  "sanction": {{"effect": "{effects}",
  "min": 數字或省略, "max": 數字或省略, "amount_text": "原文或省略", "repeating": false,
  "discretionary": false,
  "punishes": "同一個單元裡受罰的那個陳述的 id，沒有就省略",
  "triggers": [{{"statement": "同一個單元裡作為前提的罰則 id", "kind": "{trigger_kinds}"}}]}}}}]}}
{conventions}規則：
1. 引文必須逐字出現在原文；不要輸出原文沒有的內容。
2. 只有 kind 為 prescriptive 的陳述才有 deontic；constitutive（定義、適用範圍、授權）與
   sanction（罰則與其他不利效果）不要寫 deontic。
3. 義務（應…、不得…）的要件分兩種：scope 是誰、在什麼情形下適用；duty 是義務本身的內容。
   「…者，應…」的句型：「者」之前的條件（誰、做什麼事、在哪裡）每個子條件各一個 scope 要件，
   「應」之後是 duty 要件；沒有 scope 的義務會被判成對任何人都適用，沒有 duty 的義務無法被評估。
4. 罰則（sanction）要有 sanction 物件，effect 只能選一個值。受罰的陳述就在這個單元裡時，
   punishes 填它的 id；受罰的是原文引用的別條（例如「違反第七條規定者」）時省略 punishes，
   程式會連到被引用條文的陳述。非 sanction 的陳述不要寫 sanction 物件。
5. 每個要件都要有 threshold：成立與否的要件（是否排放、是否符合標準）寫 comparator "eq"、
   threshold 1；「非」「不」用 negated: true 表示；原文有數值門檻時才寫它的數字與單位。
6. 構成要件的不確定法律概念（情節重大、必要時）的要件 judgement 設為 true。效果面的「得」
   （得處、得命）不是要件：sanction 的 discretionary 設為 true，「應」為 false。
7. conditions 是一個平面清單，義務本身也是其中 role 為 duty 的一項；不要另外寫 duty 欄位。clause
   是「或」的分組：同一個 clause 裡的要件必須全部成立，不同 clause 之間是「或」，一般都用 1。
8. 主體的列舉（甲、乙或丙）寫成 slots.subject 的清單，每個主體一項（彼此是「或」），不要把每一個
   主體各寫成一個要件；客體等其他槽位的列舉同理。
9. 原文是款或目（半句話）時，主體與處罰的種類、額度常寫在「上層條文」：罰則照上層條文寫
   sanction（amount_text 逐字取自上層條文），quote 仍取自原文。沒有上層條文的單元不適用。
10. 要件在判斷「主體或對象是否屬於本法定義的某個用語」時，concept 寫該主體或對象的類別概念，
   comparator 用 eq，value_concept 填下方清單裡那個用語的代碼，且不要寫 threshold。只能用清單裡的
   代碼；清單沒有的就不要寫 value_concept（用原本的 threshold 寫法）。
   清單只給 value_concept 用；要件的 concept 要依約定 G 自己宣告。
11. 主體的責任基礎寫在 slots.subject 的項目上（"liability"）：因自己的作為或不作為負責為 act，
   因對物（土地、設施）的支配負責為 state，代表或受指派而承擔（有代表權之人、負責人員）為
   representative；原文看不出來就省略。只有 subject 可以寫 liability。
12. 門檻值不是寫在條文裡、而是許可證或核定的配額所載（「依許可證內容」「超過許可排放量」）時，
   該要件寫 permit：許可種類的概念代碼（在 concepts 宣告），不要寫 threshold。
13. 限值（約定 F）寫成 role 為 limit 的要件：concept 是受管制的項目，comparator、threshold、unit
   照原文，judgement 為 false。原文（或上層條文）寫明認定方式時，檢測方法（連續自動監測、人工採樣
   檢測…）寫 method，平均時段（即時值、一小時平均值、日平均值、數次採樣的算術平均值…）寫 averaging，
   兩者都是在 concepts 宣告的代碼；原文沒寫就省略。只有 limit 可以寫 method 與 averaging。
只輸出 JSON。

{terms}{context}原文（{law} {path}）：
{text}
""")
    .replace("{effects}", "|".join(LEGAL_EFFECTS))
    .replace("{trigger_kinds}", "|".join(TRIGGER_KINDS))
)


REPAIR = """{prompt}

你上一次的輸出：
{previous}

它有下列問題。請修正後重新輸出完整的 JSON，只輸出 JSON：
{issues}
"""

MAX_REPAIRS = 2


def _nullable(inner: dict[str, Any]) -> dict[str, Any]:
    """模型常把選填欄位照樣板填成空值：Schema 容許 null，由後面的整形與驗證處理。"""
    return {"anyOf": [inner, {"type": "null"}]}


_STR: dict[str, Any] = {"type": "string"}
_NUM: dict[str, Any] = {"type": "number"}
_BOOL: dict[str, Any] = {"type": "boolean"}
_SLOT_VALUE: dict[str, Any] = {
    "type": "object",
    "properties": {"literal": _STR, "liability": {"enum": [*LIABILITY_BASES, None]}},
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


def terms_block(defined: Mapping[str, str] | None) -> str:
    """放進提示的本法用語清單；沒有已定義的用語就什麼都不放。"""
    if not defined:
        return ""
    lines = "\n".join(f"- {code}：{name}" for code, name in defined.items())
    return f"本法已定義的用語（代碼：名稱）：\n{lines}\n\n"


def _literal(value: Any) -> Any:
    """槽位值裡的字串是字面；清單逐項處理。"""
    if isinstance(value, str):
        return {"literal": value}
    if isinstance(value, list):
        return [_literal(v) for v in value]
    return value


def _liability(role: str, value: Any) -> Any:
    """責任基礎只屬主體、只取列舉值；別處或別的值當作沒寫（載入器會因它拒絕整份文件）。"""
    if isinstance(value, list):
        return [_liability(role, v) for v in value]
    if (
        isinstance(value, Mapping)
        and "liability" in value
        and (role != "subject" or value["liability"] not in LIABILITY_BASES)
    ):
        return {k: v for k, v in value.items() if k != "liability"}
    return value


def _tidy_condition(c: Mapping[str, Any]) -> dict[str, Any]:
    """一個要件的形狀修正（見 `tidy`）。"""
    drop: set[str] = set()
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
    loaded: Loaded | None
    #: 丟掉的輸出：引文不在原文的陳述，或違反資料庫約束的整份文件
    dropped: tuple[str, ...]
    #: 受罰陳述在別的單元的罰則：等所有單元抽完，由 `link.link_penalties` 連結
    pending: tuple[Mapping[str, Any], ...] = ()
    counts: Counts = Counts()


def _unit_text(conn: Conn, law: str, path: str) -> tuple[int, str]:
    """單元的 provision id 與現行文字。"""
    row = conn.execute(
        "SELECT p.id, v.content FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
        "   AND v.content IS NOT NULL ORDER BY v.id DESC LIMIT 1",
        (law, path),
    ).fetchone()
    if row is None:
        raise LookupError(f"no current text for {law} {path}")
    return int(str(row[0])), str(row[1])


def _cites_elsewhere(path: str, text: str) -> bool:
    """原文引用別條而未引用自己：處罰的是被引用的條，不是單元內其他陳述。"""
    cited = citations(text)
    return bool(cited) and not any(path == c or path.startswith(c + "/") for c in cited)


def _held(s: Mapping[str, Any], ids: set[str], elsewhere: bool) -> bool:
    """罰則是否處罰原文引用的別條：未指明同單元的受罰陳述（或 id 不在此單元），或原文引用了別條。

    模型會把罰則連到同單元重述義務的陳述（「違反第三十八條第三項規定，…者，處…」）；
    引用閘門雖會擋下，卻白丟一個連得上的罰則，故原文引別條一律交給連結。
    """
    sanction = s.get("sanction")
    return isinstance(sanction, Mapping) and (str(sanction.get("punishes")) not in ids or elsewhere)


def _waiting(statements: Sequence[Mapping[str, Any]], ids: set[str], elsewhere: bool) -> set[str]:
    """留待連結的罰則 id：等連結者，加上處罰它們的罰則（同一條原文引用同一批條文）。"""
    waiting = {str(s.get("id")) for s in statements if _held(s, ids, elsewhere)}
    while True:
        more = {
            str(s.get("id"))
            for s in statements
            if isinstance(s.get("sanction"), Mapping)
            and str(s["sanction"].get("punishes")) in waiting
            and str(s.get("id")) not in waiting
        }
        if not more:
            return waiting
        waiting |= more


def _linkable(statements: list[dict[str, Any]], dropped: list[str]) -> list[dict[str, Any]]:
    """丟掉指向文件外的罰則與例外（連帶丟掉指向被丟者的陳述），直到不再變動。"""
    while True:
        ids = {str(s.get("id")) for s in statements}
        kept: list[dict[str, Any]] = []
        for s in statements:
            sanction = s.get("sanction")
            if isinstance(sanction, Mapping) and str(sanction.get("punishes")) not in ids:
                dropped.append(f"{s.get('id', '?')}: sanction punishes nothing in this unit")
            elif s.get("exception_of") is not None and str(s["exception_of"]) not in ids:
                dropped.append(f"{s.get('id', '?')}: excepts nothing in this unit")
            else:
                kept.append(s)
        if len(kept) == len(statements):
            return kept
        statements = kept


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


def _linkable_held(held: list[dict[str, Any]], dropped: list[str]) -> list[dict[str, Any]]:
    """待連結罰則的例外須指向同批陳述；指向別處的丟掉。"""
    while True:
        ids = {str(s.get("id")) for s in held}
        kept = [s for s in held if s.get("exception_of") is None or str(s["exception_of"]) in ids]
        for s in held:
            if s not in kept:
                dropped.append(f"{s.get('id', '?')}: excepts nothing in this unit")
        if len(kept) == len(held):
            return kept
        held = kept


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
) -> Extracted:
    """抽取一個條文單元。`agent` 記為推導者（預設為模型的名字）；驗證者不得與它相同。"""
    provision_id, text = _unit_text(conn, law, path)
    terms = terms_block(defined)
    context = ancestors_text(conn, provision_id)
    prompt = PROMPT.format(
        law=law,
        path=path,
        text=text,
        terms=terms,
        context=context_block(context),
    )
    try:
        raw = _complete(model, prompt)
    except ModelError as e:
        # 重試用盡：不寫入，下次仍是「還沒抽」
        return Extracted(None, (f"<model error: {e}>"[:300],), counts=Counts(calls=1))
    calls, repairs = 1, 0
    answer: Mapping[str, Any] | None = object_from(raw)
    answer = None if answer is None else tidy(answer, defined)
    syntax_failures = 1 if answer is None else 0
    shape_failures = (
        0 if answer is None else sum(len(v) for v in shape_issues(answer, defined).values())
    )
    for _ in range(MAX_REPAIRS):
        if answer is None:
            break
        here = [x for x in _statements_of(answer) if locate(str(x.get("quote", "")), text) >= 0]
        shape = shape_issues(answer, defined)
        issues = (
            completeness_issues(here)
            + borrowed_issues(here, defined)
            + sanction_issues(here)
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
        if not issues:
            break
        calls += 1
        repairs += 1
        try:
            reply = _complete(
                model, REPAIR.format(prompt=prompt, previous=raw, issues="\n".join(issues))
            )
        except ModelError:
            break  # 修復失敗：沿用現有版本，缺的由後面的丟棄與驗證處理
        again = object_from(reply)
        if again is None:
            break
        raw, answer = json.dumps(again, ensure_ascii=False), tidy(again, defined)
    counts = Counts(
        calls=calls,
        repairs=repairs,
        syntax_failures=syntax_failures,
        shape_failures=shape_failures,
    )
    if answer is None:
        return Extracted(None, ("<unparseable model output>",), counts=counts)
    statements: list[dict[str, Any]] = []
    dropped: list[str] = []
    broken = unusable(
        [x for x in _statements_of(answer) if locate(str(x.get("quote", "")), text) >= 0]
    )
    broken |= criminal_penalties(
        [x for x in _statements_of(answer) if locate(str(x.get("quote", "")), text) >= 0],
        text,
        context,
    )
    for ident in shape_issues(answer, defined):
        broken.setdefault(ident, "malformed conditions")
    for s in _statements_of(answer):
        quote = str(s.get("quote", ""))
        start = locate(quote, text)
        if start < 0:
            dropped.append(f"{s.get('id', '?')}: quote is not in the text")
            continue
        if str(s.get("id", "?")) in broken:
            dropped.append(f"{s.get('id', '?')}: {broken[str(s.get('id', '?'))]} after repair")
            continue
        item = {k: v for k, v in s.items() if k != "quote"}
        if item.get("kind") != "prescriptive":
            item.pop("deontic", None)  # 只有規範性陳述有義務方向（提示規則 2）
        sanction = item.get("sanction")
        if not (isinstance(sanction, Mapping) and sanction.get("effect")):
            # 模型常把罰則欄位照樣板填成空值：沒有效果就不是罰則
            item.pop("sanction", None)
            if item.get("kind") == "sanction":
                dropped.append(f"{s.get('id', '?')}: penalty without a sanction effect")
                continue
        elif item.get("kind") != "sanction":
            del item["sanction"]  # 罰則只屬於 sanction
        item |= {"law": law, "path": path, "text": text}
        if len(quote) != len(text):
            item["span"] = [start, start + len(quote)]
        statements.append(item)
    ids = {str(s.get("id")) for s in statements}
    waiting = _waiting(statements, ids, _cites_elsewhere(path, text))
    held = _local_triggers(
        _linkable_held([s for s in statements if str(s.get("id")) in waiting], dropped), dropped
    )
    statements = _local_triggers(
        _linkable([s for s in statements if str(s.get("id")) not in waiting], dropped), dropped
    )
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
    pending = (base | {"statements": held},) if held else ()
    if not statements:
        return Extracted(None, tuple(dropped), pending, counts)
    doc = base | {"statements": statements}
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        # 違反約束的文件整份不寫（`load` 已回滾），記下來，不拖垮管線
        return Extracted(None, (*dropped, f"<invalid document: {e}>"), pending, counts)
    return Extracted(loaded, tuple(dropped), pending, counts)
