"""列舉：讀清單頁，得出這次要處理的工作項（公報的包、函釋的列）。"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from envcite_jobkit import EndpointError, Policy, backoff_delay
from envcite_ontology.acquire import gazette, moenv
from envcite_ontology.db import Conn, fetch
from envcite_ontology.guidance_sync.client import Client

log = logging.getLogger(__name__)

#: 一個葉分類最多翻幾頁；觸及時告警（02-01 §7.1）
MAX_PAGES = 200
#: 連續幾頁沒有資料就判定葉分類走完（網站的總筆數不可信）
EMPTY_PAGES = 3


def retrying[T](fn: Callable[[], T], policy: Policy, sleep: Callable[[float], None]) -> T:
    """清單頁不是工作項：端點問題就地退避重試，用盡則拋出，整次失敗（結束碼 1）。"""
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except EndpointError as e:
            if attempt == policy.max_attempts:
                raise
            delay = backoff_delay(
                attempt, base=policy.backoff_base_seconds, cap=policy.backoff_max_seconds
            )
            log.warning("%s，%.0f 秒後重試（第 %d 次）", e, delay, attempt)
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def gazette_packs(
    conn: Conn,
    client: Client,
    policy: Policy,
    sleep: Callable[[float], None],
    *,
    since: date | None,
    rescan: bool,
    today: date,
) -> list[gazette.Pack]:
    """本月的每日包與 `since` 起的歷史月包；未給 `since` 時從上個月一日起。"""
    if since is None:
        since = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    listings = [gazette.MONTH_LIST] + [
        gazette.YEAR_LIST.format(year=year - 1911) for year in range(since.year, today.year + 1)
    ]
    found: dict[str, gazette.Pack] = {}
    for url in listings:
        listing = retrying(lambda: client.text(url), policy, sleep)  # noqa: B023
        for p in gazette.packs(listing):
            if p.covers_from(since):
                found.setdefault(p.name, p)
    done = set() if rescan else _origins(conn, "gazette")
    return sorted((p for p in found.values() if p.name not in done), key=lambda p: p.name)


def moenv_rows(
    conn: Conn,
    client: Client,
    policy: Policy,
    sleep: Callable[[float], None],
    *,
    since: date | None,
    rescan: bool,
) -> Crawl:
    """走全部葉分類；清單依發文日新到舊，整頁都早於 `since` 就不再往後翻。"""

    def get(url: str) -> str:
        return retrying(lambda: client.text(url), policy, sleep)

    leaves: list[str] = []
    for parent in moenv.parents(get(moenv.QUERY)):
        leaves += moenv.leaves(get(moenv.LIST.format(feid=parent, page=1))) or [parent]
    found: dict[str, moenv.Row] = {}
    complete = True
    for leaf in dict.fromkeys(leaves):
        empty = 0
        for page in range(1, MAX_PAGES + 1):
            rows = moenv.rows(get(moenv.LIST.format(feid=leaf, page=page)))
            if not rows:
                empty += 1
                if empty == EMPTY_PAGES:
                    break
                continue
            empty = 0
            for r in rows:
                if since is None or r.listed_on >= since:
                    found.setdefault(r.site_id, r)
            if since is not None and all(r.listed_on < since for r in rows):
                break
        else:
            log.warning("葉分類 %s 翻到 %d 頁仍有資料，未走完", leaf, MAX_PAGES)
            complete = False
    done = set() if rescan else _keys(conn, "moenv_guidance")
    return Crawl(
        rows=[r for r in found.values() if r.document_no not in done],
        seen=frozenset(r.document_no for r in found.values()),
        complete=complete,
    )


@dataclass(frozen=True)
class Crawl:
    """走訪函釋清單的結果。"""

    #: 這次要處理的列
    rows: Sequence[moenv.Row]
    #: 清單上出現的每個字號（含已處理過而略過的）
    seen: frozenset[str]
    #: 每個葉分類都走到了空頁
    complete: bool


class _TextRow(NamedTuple):
    value: str


def _origins(conn: Conn, feed: str) -> set[str]:
    rows = fetch(
        conn,
        "SELECT origin_name FROM source.snapshot WHERE feed = %s AND origin_name IS NOT NULL",
        (feed,),
        _TextRow,
    )
    conn.rollback()
    return {r.value for r in rows}


def _keys(conn: Conn, feed: str) -> set[str]:
    rows = fetch(conn, "SELECT key FROM source.item WHERE feed = %s", (feed,), _TextRow)
    conn.rollback()
    return {r.value for r in rows}
