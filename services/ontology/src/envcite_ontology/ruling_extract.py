"""由解釋文（判決主文）的原文提出違憲失效的候選：哪部法規的哪一條，自何日起失效。

**只在寫法明確時才提**。解釋文是自然語言，沒有結構化欄位；規則刻意保守，寧可漏提也不錯提——
漏提的裁判仍留在 `corpus.constitutional_ruling`（附原文），日後規則放寬（遞增 `EXTRACTOR_VERSION`）
即可補上，錯提則會讓「這一條是否仍有效」的答案悄悄出錯。

一個句子要提出候選，必須同時滿足：
- 有宣告失效的用語（失其效力、不再適用、停止適用）與違憲的用語（違憲、有違、不符、牴觸、違反）；
- 去掉引號內的條文原文與憲法條號後，失效用語之前**恰有一個**
  「法規名稱＋第 N 條（之 K）（第 M 項）」，
  法規名稱必須是語料裡的法規標題；多個條號、「同條」「同法」這類回指、命令的函釋都不提；
- 失效日寫法明確：自本解釋公布（判決宣示、公告）之日起、至遲於屆滿 N 年（月）、
  自中華民國某年某月某日起；「逾期未…」之類附條件的不提。

條文單元只到項：款、目不另提；裁判只宣告條文的一部分（「關於…部分」「於此範圍內」）時記
`partial`，失效仍記在整個條文單元上，核對時看原文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from envcite_ontology.ingest import numerals

#: 抽取規則的版本；改規則要遞增，已掃描的裁判才會重新評估。
EXTRACTOR_VERSION = 1

_EFFECT = re.compile(r"失其效力|不再適用|停止適用|失效")
_VIOLATION = re.compile(r"違憲|有違|不符|牴觸|抵觸|違反")
_QUOTED = re.compile(r"「[^「」]*」|『[^『』]*』")
_NUM = rf"(?:\d+|[{numerals.CN_NUMERALS}]+)"
_CONSTITUTION = re.compile(rf"憲法(?:增修條文)?第{_NUM}條(?:之{_NUM})?(?:第{_NUM}項)?")
_ANY_ARTICLE = re.compile(rf"第{_NUM}條")
_REF = re.compile(rf"第({_NUM})條(?:之({_NUM}))?(?:第({_NUM})項)?")
_PARTIAL = re.compile(r"關於|部分|範圍")
_CONDITIONAL = re.compile(r"逾期|屆期|期限內未|未完成")
_DEADLINE = re.compile(rf"至遲於?屆滿({_NUM})(年|個月|月)")
_ARABIC_DATE = re.compile(r"自(?:中華)?民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日起")
_CN_DATE = re.compile("自" + numerals.DATE_WITH_ERA.pattern + "起")
_FROM_RULING = re.compile(r"自本(?:解釋|判決|裁判)(?:公布|公告|宣示|宣判)?(?:之)?日起")


@dataclass(frozen=True, slots=True)
class Candidate:
    #: 法規標題（呼叫端以標題對到法規）
    law_title: str
    path: str
    invalid_from: date
    partial: bool


def should_keep(text: str) -> bool:
    """這份主文有沒有宣告失效：沒有的裁判不入庫。"""
    return bool(_EFFECT.search(text) and _VIOLATION.search(text))


def extract(text: str, decided_on: date, titles: frozenset[str]) -> list[Candidate]:
    """`titles` 是語料裡法規標題的集合。無法確定的句子不提。"""
    if not titles:
        return []
    by_length = "|".join(re.escape(t) for t in sorted(titles, key=len, reverse=True))
    title_ref = re.compile(rf"({by_length})({_REF.pattern})")
    found: dict[tuple[str, str], Candidate | None] = {}
    for sentence in re.split(r"(?<=。)", text.replace("\n", "")):
        candidate = _sentence(sentence, decided_on, title_ref)
        if candidate is None:
            continue
        key = (candidate.law_title, candidate.path)
        # 同一單元的兩個說法不一致：兩個都不提。
        found[key] = candidate if found.get(key, candidate) == candidate else None
    return [c for c in found.values() if c is not None]


def _sentence(s: str, decided_on: date, title_ref: re.Pattern[str]) -> Candidate | None:
    effect = _EFFECT.search(s)
    if effect is None or not _VIOLATION.search(s) or "維持" in s:
        return None
    head = _CONSTITUTION.sub("", _QUOTED.sub("", s[: effect.end()]))
    refs = list(title_ref.finditer(head))
    if len(refs) != 1 or len(_ANY_ARTICLE.findall(head)) != 1:
        return None
    ref = refs[0]
    tail = head[ref.end() :]
    if _CONDITIONAL.search(tail):
        return None
    when = _date(tail, decided_on)
    if when is None:
        return None
    article, branch, paragraph = (_int(ref.group(i)) for i in (3, 4, 5))
    path = f"art_{article}" + (f"-{branch}" if branch else "")
    if paragraph:
        path += f"/para_{paragraph}"
    return Candidate(ref.group(1), path, when, bool(_PARTIAL.search(tail)))


def _date(tail: str, decided_on: date) -> date | None:
    if (m := _DEADLINE.search(tail)) is not None:
        amount = _int(m.group(1))
        return _add_months(decided_on, amount * (12 if m.group(2) == "年" else 1))
    if _FROM_RULING.search(tail):
        return decided_on
    if (m := _ARABIC_DATE.search(tail)) is not None:
        year, month, day = (int(g) for g in m.groups())
        try:
            return date(year + 1911, month, day)
        except ValueError:
            return None
    if (m := _CN_DATE.search(tail)) is not None:
        return numerals.roc_date(*m.groups())
    return None


def _int(s: str | None) -> int:
    if not s:
        return 0
    return int(s) if s.isdigit() else numerals.parse_int(s)


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover
