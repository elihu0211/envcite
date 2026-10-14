"""生效日的推導（envcite-docs 03-03 §6）。

依據中央法規標準法：第 13 條，明定自公布或發布日施行者，自公布或發布之日起算至第三日
起生效；第 14 條，特定施行日期者，自該日起生效；第 22 條第 3 項，廢止之法規自公布或
發布之日起算至第三日起失效。

只有現行全文，所以只判定最後一次公布或發布所生的版本。每一條的判定依序：

1. 全國法規資料庫的施行說明列出的條：待確認。
2. 最後一次公布沒有觸及該條：文字在此版本之前已存在，以版本日為界（unchanged）。
3. 最後一次公布的沿革語句，或（沒有時）末條，明定自公布或發布日施行：第三日（promulgation）。
4. 同上，明定特定日期：該日（specified）。
5. 明定施行日由命令定之：尚未施行（by_order）。
6. 其餘：待確認，不推定第 13 條（unconfirmed）。

施行說明沒有列出條號時，第 3 至 6 步換成以施行日欄印證後的整次公布施行日（`from_note`）；
印證不了者，被觸及的條待確認。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from envcite_ontology.ingest.articles import article_path, sort_key
from envcite_ontology.ingest.numerals import DATE_BARE, roc_date

# 沿革語句與條文都可能寫成「並自」「自」，前面是分句起點；「第 46 條自…」是條別的施行日，不算。
_CLAUSE = r"(?:^|[；，。：]|本[^；，。：第]{1,8}?)(?:並|且)?自"
_PROMULGATION = re.compile(rf"{_CLAUSE}(?:公布|發布)(?:之)?日(?:起)?(?:施行|生效)")
_DATE = re.compile(rf"{_CLAUSE}(?P<date>{DATE_BARE.pattern})(?:起)?(?:施行|生效)")
_ORDERED = re.compile(rf"[公發]布(?:.*?)定[自於](?P<date>{DATE_BARE.pattern})(?:起)?(?:施行|生效)")
_EXCEPTION = re.compile(r"除.{0,30}者外")
_BY_ORDER = re.compile(r"施行日(?:期)?.{0,20}(?:命令|令)?定之")

_ARTICLE_LIST = re.compile(
    r"第([0-9]+(?:-[0-9]+)?(?:(?:[、，,～~及]|條?至)第?[0-9]+(?:-[0-9]+)?)*)條"
)

#: 來源以此日期表示「施行日期由命令定之」（LawEffectiveDate）。
BY_ORDER_SENTINEL = date(9999, 12, 31)

PROMULGATION_DAYS = 2  # 「自公布或發布之日起算至第三日」：公布當日為第一日。


@dataclass(frozen=True)
class Commencement:
    rule: str  # unchanged | promulgation | specified | by_order | unconfirmed
    start: date | None


def touched_articles(entry: str, known: Iterable[str]) -> set[str]:
    """沿革語句列出的條（`art_N` 路徑）。「第 39～39-5 條」依實際存在的條展開。"""
    order = sorted(known, key=sort_key)
    compact = re.sub(r"\s+", "", entry)
    touched: set[str] = set()
    for m in _ARTICLE_LIST.finditer(compact):
        for part in re.split(r"[、，,及]", m.group(1)):
            ends = [
                p
                for e in re.split(r"[～~]|條?至", part)
                if (p := article_path(e.removeprefix("第")))
            ]
            if not ends:
                continue
            lo, hi = sort_key(ends[0]), sort_key(ends[-1])
            touched.update(ends)
            touched.update(p for p in order if lo <= sort_key(p) <= hi)
    return touched


def from_note(note: str, effective: date | None, version_date: date) -> Commencement:
    """施行說明沒有列出任何條號時，整次公布的施行日。

    以說明內的日期印證施行日欄（`LawEffectiveDate`），吻合才採用；欄位 9999-12-31 為由命令
    定之。有「除…者外」、日期對不上、或相對表達（「某法施行之日」）者不猜。
    """
    compact = re.sub(r"\s+", "", note)
    if _EXCEPTION.search(compact):
        return Commencement("unconfirmed", None)
    if effective == BY_ORDER_SENTINEL:
        return Commencement("by_order", None)
    dates = {roc_date(m.group(1), m.group(2), m.group(3)) for m in DATE_BARE.finditer(compact)}
    if effective is not None and effective in dates - {version_date}:
        return Commencement("specified", effective)
    return Commencement("unconfirmed", None)


def derive(
    *,
    version_date: date,
    entry: str,
    last_article: str,
    deferred: bool,
    touched: bool,
    note: Commencement | None = None,
) -> Commencement:
    """一條在現行版本的生效日；`entry` 是最後一次公布的沿革語句，`note` 見 `from_note`。"""
    if deferred:
        return Commencement("unconfirmed", None)
    if not touched:
        return Commencement("unchanged", version_date)
    if note is not None:
        return note
    for source in (entry, last_article):
        found = _from_text(source, version_date)
        if found is not None:
            return found
    return Commencement("unconfirmed", None)


def _from_text(text: str, version_date: date) -> Commencement | None:
    compact = re.sub(r"\s+", "", text)
    if _EXCEPTION.search(compact):
        return Commencement("unconfirmed", None)
    if _PROMULGATION.search(compact):
        return Commencement("promulgation", version_date + timedelta(days=PROMULGATION_DAYS))
    m = _DATE.search(compact)
    if m is not None:
        when = roc_date(m.group(2), m.group(3), m.group(4))
        return Commencement("specified", when) if when else Commencement("unconfirmed", None)
    if _BY_ORDER.search(compact):
        return Commencement("by_order", None)
    return None


def ordered_start(entry: str) -> date | None:
    """施行命令（「行政院令發布定自某日施行」）所定的日期。"""
    m = _ORDERED.search(re.sub(r"\s+", "", entry))
    return roc_date(m.group(2), m.group(3), m.group(4)) if m else None
