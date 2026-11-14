"""要件對引文的涵蓋：標籤沒涵蓋的字，與不可用的輸出。"""

import re
from collections.abc import Mapping, Sequence
from itertools import groupby
from typing import Any

from envcite_ontology.verification.checks.fragments import MODAL
from envcite_ontology.verification.checks.locate import VARIANTS

#: 不必寫進要件的字：標點、連接詞與助詞。
_FILLER = re.compile(r"[，、。；：「」（）()\s]|並|及|或|與|之|其|者|時|於|以|為|應|不得")


#: 要件標籤與原文的共同字串至少這麼長才算涵蓋（單字幾乎處處相同）。
_MIN_SHARED = 2


#: 漏掉的片段去掉連接詞後至少這麼長才回報。
_MIN_GAP = 3


def coverage_gaps(span: str, labels: Sequence[str]) -> list[str]:
    """原文片段中沒被任何標籤逐字涵蓋的部分（依序），去掉連接詞後太短的不算。"""
    span = span.translate(VARIANTS)
    covered = [False] * len(span)
    for label in (x.translate(VARIANTS) for x in labels):
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
        modal = MODAL.search(quote)
        if modal is None:
            continue
        named: dict[str, list[str]] = {"duty": [], "scope": _subjects(s)}
        for c in s.get("conditions") or []:
            if isinstance(c, Mapping) and c.get("role") in named:
                named[str(c["role"])].append(labels.get(str(c.get("concept")), ""))
        ident = s.get("id", "?")
        if gaps := coverage_gaps(quote[modal.end() :], named["duty"]):
            issues.append(
                f"陳述 {ident} 的 duty 要件沒有涵蓋引文的「{'」「'.join(gaps)}」：義務的每個行為"
                "與它的期限、對象、方式都是義務內容，依約定 G 用引文的字寫進 duty 概念的標籤"
                "（一個行為一個 duty）。"
            )
        if gaps := coverage_gaps(quote[: modal.start()], named["scope"]):
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
