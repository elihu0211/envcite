"""引文的完整性：碎片、條件句未接結論與缺漏的後半。"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

_CONDITIONAL = re.compile(r"者[，,]\s*(應|不得)")


#: 規範性陳述的助動詞：引文不含它，就是從規範句切下的子句（主體、適用情形或義務方向在別處）。
MODAL = re.compile(r"應|不得|須|得|禁止|始可")


def fragments(statements: Sequence[Mapping[str, Any]], text: str) -> dict[str, str]:
    """規範性陳述的引文缺助動詞而單元原文有（id → 引文）：引文是從規範句切下的子句（提示規則 14）。

    款、目常只列行為，助動詞在上層條文（「不得有下列行為：」），原文本身沒有助動詞時不判。
    """
    if not MODAL.search(text):
        return {}
    return {
        str(s.get("id", "?")): str(s.get("quote", ""))
        for s in statements
        if s.get("kind") == "prescriptive" and not MODAL.search(str(s.get("quote", "")))
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
