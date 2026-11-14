"""`sync`：列舉、逐項、覆蓋率與停止適用的串接。"""

import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from envcite_jobkit import ExitCode, Policy, RunStats, run
from envcite_ontology.acquire.web import Http
from envcite_ontology.clock import Today
from envcite_ontology.db import Conn
from envcite_ontology.guidance_anchor import Resolver
from envcite_ontology.guidance_sync.client import Client
from envcite_ontology.guidance_sync.job import Job
from envcite_ontology.guidance_sync.listing import gazette_packs, moenv_rows
from envcite_ontology.guidance_sync.repeals import apply_repeals
from envcite_ontology.guidance_sync.report import Report
from envcite_ontology.guidance_sync.settle import settle_crawl

POLICY = Policy(
    name="guidance",
    open_action="exit",
    max_attempts=4,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)


#: 對來源網站的禮貌間隔（秒）；逐頁循序抓（02-01 表 crawl-params）
PAUSE_SECONDS = 0.5


#: 隔離的項目占列舉數超過此比例，本次判定失敗
COVERAGE_TOLERANCE = 0.01


log = logging.getLogger(__name__)


def sync(
    conn: Conn,
    source: str,
    root: Path,
    *,
    since: date | None = None,
    rescan: bool = False,
    client: Client | None = None,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
    today: Today = date.today,
) -> Report:
    """取得一個來源（`gazette` 或 `moenv`）的行政規則並寫入 `guidance`，回傳報告。

    逐項的失敗由 jobkit 隔離，結果在 `Report.exit_code`；清單頁的端點問題重試用盡則
    拋出 `EndpointError`，整次失敗。來源不是這兩者拋 `ValueError`。
    """
    started = time.monotonic()
    client = client or Http(PAUSE_SECONDS)
    report = Report(source)
    resolver = Resolver(conn)
    job = Job(conn, root, report, resolver, today)
    stats = RunStats()
    if source == "gazette":
        packs = gazette_packs(
            conn, client, policy, sleep, since=since, rescan=rescan, today=today()
        )
        report.listed = len(packs)
        code = run(
            packs,
            lambda p: job.pack(client, p),
            policy,
            item_id=lambda p: p.name,
            stats=stats,
            sleep=sleep,
        )
        code = _covered(report, stats, code)
        apply_repeals(conn, report)
    elif source == "moenv":
        crawl = moenv_rows(conn, client, policy, sleep, since=since, rescan=rescan)
        report.listed = len(crawl.rows)
        code = run(
            crawl.rows,
            lambda r: job.letter(client, r),
            policy,
            item_id=lambda r: r.site_id,
            stats=stats,
            sleep=sleep,
        )
        code = _covered(report, stats, code)
        if since is None and code == ExitCode.OK:
            settle_crawl(conn, crawl, report, today())
    else:
        raise ValueError(f"unknown guidance source: {source}")
    report.exit_code = int(code)
    report.seconds = round(time.monotonic() - started, 1)
    log.info("行政規則取得：%s", report.line())
    return report


def _covered(report: Report, stats: RunStats, code: ExitCode) -> ExitCode:
    """隔離的項目超過列舉數的 1%，本次判定失敗（02-01 §7.1）。"""
    report.processed, report.dead_lettered = stats.processed, stats.dead_lettered
    if code == ExitCode.OK and report.dead_lettered > COVERAGE_TOLERANCE * report.listed:
        log.error("覆蓋率不足：%d／%d 項被隔離", report.dead_lettered, report.listed)
        return ExitCode.FAILED
    return code
