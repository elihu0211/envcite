"""抽取器：由語言模型從條文單元寫出標註，狀態為 extracted。

模型不負責錨點：每個陳述附 `quote`，span、雜湊、`text` 由這裡在原文定位；引文不在原文的陳述丟掉。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from envcite_ontology.annotate import AnnotationError, Loaded, load
from envcite_ontology.verification.context import ancestors_text, context_block
from envcite_ontology.verification.conventions import apply as with_conventions
from envcite_ontology.verification.gates import citations
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError

Conn = psycopg.Connection[tuple[object, ...]]

PROMPT = with_conventions("""你是法規標註器。下面是一個條文單元的原文。
請只依原文，輸出一個 JSON 物件：
{{"concepts": [{{"code": "英文小寫底線的代碼", "label": "中文名稱"}}],
  "statements": [{{"id": "短識別", "quote": "原文中的一段，逐字複製", "kind":
  "prescriptive|constitutive|sanction", "deontic": "obligation|prohibition|permission 或省略",
  "slots": {{"subject|action|object|medium": {{"literal": "…"}}}},
  "conditions": [{{"role": "scope|duty", "clause": 1, "concept": "代碼", "comparator":
  "le|lt|ge|gt|eq|ne", "threshold": 數字, "unit": "單位或省略", "negated": false,
  "judgement": false}}],
  "sanction": {{"effect": "fine|order|restriction|deprivation|reputation|warning|confiscation",
  "min": 數字或省略, "max": 數字或省略, "amount_text": "原文或省略", "repeating": false,
  "discretionary": false,
  "punishes": "同一個單元裡受罰的那個陳述的 id，沒有就省略"}}}}]}}
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
8. 主體的列舉（甲、乙或丙）寫在 slots.subject，不要把每一個主體各寫成一個要件。
9. 原文是款或目（半句話）時，主體與處罰的種類、額度常寫在「上層條文」：罰則照上層條文寫
   sanction（amount_text 逐字取自上層條文），quote 仍取自原文。沒有上層條文的單元不適用。
只輸出 JSON。

{context}原文（{law} {path}）：
{text}
""")


REPAIR = """{prompt}

你上一次的輸出：
{previous}

它有下列問題。請修正後重新輸出完整的 JSON，只輸出 JSON：
{issues}
"""

MAX_REPAIRS = 2

_CONDITIONAL = re.compile(r"者[，,]\s*(應|不得)")


def completeness_issues(statements: Sequence[Mapping[str, Any]]) -> list[str]:
    """義務要有 duty 要件、「…者，應…」要有 scope 要件；只查結構有沒有寫，不查對錯。"""
    issues: list[str] = []
    for s in statements:
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


def tidy(answer: Mapping[str, Any]) -> dict[str, Any]:
    """不改變意思的形狀修正：槽位直接寫成字串（`"subject": "甲"`）就是它的字面。"""
    out = dict(answer)
    out["statements"] = [
        {
            **s,
            "slots": {k: {"literal": v} if isinstance(v, str) else v for k, v in slots.items()},
        }
        if isinstance(slots := s.get("slots"), Mapping)
        else dict(s)
        for s in _statements_of(answer)
    ]
    return out


def shape_issues(answer: Mapping[str, Any]) -> dict[str, list[str]]:
    """載入器必然拒絕的形狀（id → 問題）：要件非物件、無 concept、或 concept 未宣告。

    載入器一遇到就整份不寫、連帶丟掉正確的陳述；故先請模型修，修不好才只丟該陳述。
    """
    declared = {str(c["code"]) for c in _concepts_of(answer)}
    out: dict[str, list[str]] = {}
    for s in _statements_of(answer):
        problems: list[str] = []
        for c in s.get("conditions") or []:
            if not isinstance(c, Mapping):
                problems.append("要件必須是物件（role、concept、comparator、threshold…）。")
                continue
            for field in ("concept", "value_concept"):
                code = c.get(field)
                if field == "concept" and not code:
                    problems.append(
                        f"有一個要件沒有 concept（role={c.get('role')}）：每個要件都要指名一個"
                        "在 concepts 裡宣告過的代碼。"
                    )
                elif code and str(code) not in declared:
                    problems.append(
                        f"要件的 {field}「{code}」沒有在 concepts 裡宣告（code 與 label 都要寫）。"
                    )
        if problems:
            out[str(s.get("id", "?"))] = problems
    return out


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
class Extracted:
    loaded: Loaded | None
    #: 丟掉的輸出：引文不在原文的陳述，或違反資料庫約束的整份文件
    dropped: tuple[str, ...]
    #: 受罰陳述在別的單元的罰則：等所有單元抽完，由 `link.link_penalties` 連結
    pending: tuple[Mapping[str, Any], ...] = ()


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
) -> Extracted:
    """抽取一個條文單元。`agent` 記為推導者（預設為模型的名字）；驗證者不得與它相同。"""
    provision_id, text = _unit_text(conn, law, path)
    prompt = PROMPT.format(
        law=law, path=path, text=text, context=context_block(ancestors_text(conn, provision_id))
    )
    try:
        raw = model.complete(prompt)
    except ModelError as e:
        # 重試用盡：不寫入，下次仍是「還沒抽」
        return Extracted(None, (f"<model error: {e}>"[:300],))
    answer: Mapping[str, Any] | None = object_from(raw)
    answer = None if answer is None else tidy(answer)
    for _ in range(MAX_REPAIRS):
        if answer is None:
            break
        here = [x for x in _statements_of(answer) if str(x.get("quote", "")) in text]
        shape = shape_issues(answer)
        issues = completeness_issues(here) + [
            f"陳述 {ident}：{problem}"
            for x in here
            for ident in [str(x.get("id", "?"))]
            for problem in shape.get(ident, [])
        ]
        if not issues:
            break
        try:
            reply = model.complete(
                REPAIR.format(prompt=prompt, previous=raw, issues="\n".join(issues))
            )
        except ModelError:
            break  # 修復失敗：沿用現有版本，缺的由後面的丟棄與驗證處理
        again = object_from(reply)
        if again is None:
            break
        raw, answer = json.dumps(again, ensure_ascii=False), tidy(again)
    if answer is None:
        return Extracted(None, ("<unparseable model output>",))
    statements: list[dict[str, Any]] = []
    dropped: list[str] = []
    broken = unusable([x for x in _statements_of(answer) if str(x.get("quote", "")) in text])
    for ident in shape_issues(answer):
        broken.setdefault(ident, "malformed conditions")
    for s in _statements_of(answer):
        quote = str(s.get("quote", ""))
        start = text.find(quote) if quote else -1
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
        if quote != text:
            item["span"] = [start, start + len(quote)]
        statements.append(item)
    ids = {str(s.get("id")) for s in statements}
    waiting = _waiting(statements, ids, _cites_elsewhere(path, text))
    held = _linkable_held([s for s in statements if str(s.get("id")) in waiting], dropped)
    statements = _linkable([s for s in statements if str(s.get("id")) not in waiting], dropped)
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
        return Extracted(None, tuple(dropped), pending)
    doc = base | {"statements": statements}
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        # 違反約束的文件整份不寫（`load` 已回滾），記下來，不拖垮管線
        return Extracted(None, (*dropped, f"<invalid document: {e}>"), pending)
    return Extracted(loaded, tuple(dropped), pending)
