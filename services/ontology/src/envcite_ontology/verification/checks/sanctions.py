"""罰則：效果子句、刑罰、所處罰的陳述與觸發的整理。"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from envcite_ontology.claims.enums import TRIGGER_KINDS
from envcite_ontology.violation import citations

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
    ident: str, trigger: object, effects: Mapping[str, Any]
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


def settled_sanctions(
    statements: list[dict[str, Any]], path: str, text: str, dropped: list[str]
) -> list[dict[str, Any]]:
    """罰則的受罰者與觸發整理成載入器接受的樣子；丟掉的記進 `dropped`。"""
    duties = {str(s.get("id")) for s in statements if s.get("kind") == "prescriptive"}
    elsewhere = _cites_elsewhere(path, text)
    return _local_triggers([_aimed(s, duties, elsewhere) for s in statements], dropped)
