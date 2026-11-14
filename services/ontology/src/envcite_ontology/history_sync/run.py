"""`sync`：依步驟串接取得、匯入與檢索投影。"""

import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from envcite_jobkit import ExitCode, Policy
from envcite_ontology import delegation, embedding, projection
from envcite_ontology.clock import Today
from envcite_ontology.db import Conn
from envcite_ontology.history_sources import HistorySource, Web
from envcite_ontology.history_sync.acquire import acquire
from envcite_ontology.history_sync.importing import import_
from envcite_ontology.history_sync.policy import LIMITS, POLICY
from envcite_ontology.history_sync.report import BatchRejected, Report

log = logging.getLogger(__name__)


def _write_gate(report: Report) -> ExitCode:
    """被資料庫擋下的占對到的法規超過上限就判資料不合預期。

    寫完才知道，所以在其餘法規都寫完、投影也跑完之後才判：寫進去的都是資料庫認可的，
    不必因為少數幾部被擋下而讓整批的檢索停在舊的投影。
    """
    failed = report.actions["write_rejected"]
    if not report.matched:
        return ExitCode.OK
    description, limit = LIMITS["write_rejected"]
    log.info("%s %d／%d", description, failed, report.matched)
    if failed / report.matched > limit:
        log.error("%s %.2f%%，上限 %.2f%%", description, failed / report.matched * 100, limit * 100)
        return ExitCode.FATAL
    return ExitCode.OK


def sync(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    *,
    step: str = "sync",
    rescan: bool = False,
    dry_run: bool = False,
    embedder: embedding.Embedder | None = None,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
    today: Today = date.today,
) -> int:
    """依 `step` 跑取得與匯入，回傳結束碼。

    `sync` 兩者都跑，匯入後抽取授權並重建檢索投影。結束碼的意義見模組說明。`dry_run` 只回推
    與驗收，不寫也不重建投影。
    """
    started = time.monotonic()
    report = Report()
    code: int = ExitCode.OK
    try:
        if step in ("sync", "acquire"):
            code = acquire(
                conn,
                source,
                root,
                web,
                report,
                rescan=rescan,
                policy=policy,
                sleep=sleep,
                today=today,
            )
        if code == ExitCode.OK and step in ("sync", "import"):
            import_(conn, source, root, report, dry_run=dry_run)
            if not dry_run:
                delegation.extract(conn)
                conn.commit()
                projection.rebuild(conn, embedder, policy)
                code = _write_gate(report)
    except BatchRejected as e:
        conn.rollback()
        log.error("history import rejected: %s", e)
        code = ExitCode.FATAL
    except projection.EmbeddingStopped as e:
        conn.rollback()
        code = e.code
    report.seconds = round(time.monotonic() - started, 1)
    log.info("歷史版本（%s）：%s", source.feed, report.line())
    return int(code)
