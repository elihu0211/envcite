"""`canon-sync <來源>`：取得、匯入、授權與處罰依賴圖的抽取、檢索投影依序執行。

各步冪等，每次全跑（映像升級後解析器版本改變，沒有新檔也須重匯）；任一步失敗即停。
結束碼：0 成功（含無事可做）；1 暫時性失敗，可重試；2 重試無益（資料不合預期、整批比例越線、
嵌入端點的設定錯誤）。

下載與嵌入對外部端點的重試、退避與斷路依 `POLICY`，可由 `ENVCITE_JOBKIT_*` 環境變數覆寫
（`envcite_jobkit`）。嵌入一跑數小時，偶發的端點錯誤不讓整次失敗：斷路器開啟時原地等到
半開，重試用盡的批次跳過、下次再補。
"""

import argparse
import logging
from pathlib import Path

import psycopg

from canon import projection, violation
from canon.acquire.cli import EXIT_INVALID, SOURCES
from canon.acquire.engine import InvalidSnapshot, Source, acquire
from canon.db import Conn
from canon.embedding import Embedder
from canon.law import delegation, importer
from canon.law.importer import BatchRejected
from canon.projection import EmbeddingStopped
from canon.settings import SnapshotSettings, load
from canon.verification import config
from envcite_jobkit import Policy

log = logging.getLogger(__name__)


EXIT_TRANSIENT = 1

POLICY = Policy(
    name="sync",
    open_action="wait",
    max_attempts=5,
    backoff_base_seconds=2.0,
    backoff_max_seconds=60.0,
)


def exit_code(error: BaseException) -> int:
    """資料不合預期或整批越線為 2（重試無益）；嵌入中止沿用 jobkit 的結束碼；其餘為 1。"""
    if isinstance(error, EmbeddingStopped):
        return error.code
    return EXIT_INVALID if isinstance(error, InvalidSnapshot | BatchRejected) else EXIT_TRANSIENT


def sync(
    conn: Conn,
    source: Source,
    root: Path,
    embedder: Embedder | None = None,
    policy: Policy = POLICY,
) -> int:
    """跑一個週期；失敗時 rollback 並回傳結束碼。"""
    try:
        acquire(source, root, conn, policy)
        importer.run(conn, root, source)
        delegation.extract(conn)
        violation.extract(conn)
        conn.commit()
        projection.rebuild(conn, embedder, policy)
    except Exception as e:
        conn.rollback()
        code = exit_code(e)
        log.error("sync failed (exit %d): %s", code, e)
        return code
    return 0


def main() -> None:
    """`canon-sync <來源>`：對一個來源跑完整週期（`sync`），以它的結束碼結束。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="canon-sync")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    with psycopg.connect(settings.dsn) as conn:
        embedder = config.embedder_from_settings(settings, timeout=60.0)
        policy = Policy.load(defaults=POLICY)
        raise SystemExit(sync(conn, SOURCES[args.source], settings.snapshot_dir, embedder, policy))
