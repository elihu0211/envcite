"""函釋的全量走訪之後：未出現的計數、下架與停止適用。"""

import logging
from datetime import date
from typing import NamedTuple

from envcite_ontology.acquire import moenv
from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.guidance_sync.listing import Crawl
from envcite_ontology.guidance_sync.problems import ItemRow, record_problems
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.import_problems import Problem

log = logging.getLogger(__name__)


class _SettledRow(NamedTuple):
    complete: bool
    dropped: int
    ceased: int
    reappeared: list[int]


def settle_crawl(conn: Conn, crawl: Crawl, report: Report, today: date) -> None:
    """一次全量走訪之後：未出現的計數與下架由 `guidance.settle_crawl` 轉移（門檻在遷移 0050）。"""
    if not crawl.complete:
        report.crawl_complete = False
        log.warning("有葉分類沒走完，不推論下架")
        return
    with conn.transaction():
        row = fetch_one(
            conn,
            "SELECT * FROM guidance.settle_crawl(%s, %s, %s)",
            (sorted(crawl.seen), today, moenv.BASE),
            _SettledRow,
        )
        assert row is not None
        complete, dropped, ceased, reappeared = row
        back = fetch(
            conn,
            "SELECT id, last_snapshot_id FROM source.item WHERE id = ANY(%s)",
            (reappeared,),
            ItemRow,
        )
        for item in back:
            record_problems(
                conn, report, item.id, item.last_snapshot_id, [Problem("warning", "reappeared", "")]
            )
    report.crawl_complete = complete
    if not complete:
        log.warning("沒出現的函釋超過還在架上的 5%%，視為網站異常，不推論下架")
    report.dropped += dropped
    report.cessations += ceased
