"""`env-law-sync <來源>`：取得、匯入、檢索投影依序執行（env-law-docs 03-04 §3）。

三步冪等，每次全跑（映像升級後解析器版本改變，沒有新檔也須重匯）；任一步失敗即停。
結束碼：0 成功（含無事可做）；1 暫時性失敗，可重試；2 重試無益（資料不合預期、整批比例越線）。
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import psycopg

from env_law_ontology import projection
from env_law_ontology.acquire.cli import EXIT_INVALID, SOURCES
from env_law_ontology.acquire.engine import InvalidSnapshot, Source, acquire
from env_law_ontology.ingest import importer
from env_law_ontology.ingest.importer import BatchRejected

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

EXIT_TRANSIENT = 1


def exit_code(error: BaseException) -> int:
    """資料不合預期或整批越線為 2（重試無益），其餘為 1。"""
    return EXIT_INVALID if isinstance(error, InvalidSnapshot | BatchRejected) else EXIT_TRANSIENT


def sync(conn: Conn, source: Source, root: Path) -> int:
    """跑一個週期；失敗時 rollback 並回傳結束碼。"""
    try:
        acquire(source, root, conn)
        importer.run(conn, root, source)
        projection.rebuild(conn)
    except Exception as e:
        conn.rollback()
        code = exit_code(e)
        log.error("sync failed (exit %d): %s", code, e)
        return code
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-sync")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    dsn = os.environ.get("ONTOLOGY_DSN", "")
    root = os.environ.get("ACQUIRE_SNAPSHOT_DIR", "")
    if not dsn or not root:
        raise SystemExit("ONTOLOGY_DSN and ACQUIRE_SNAPSHOT_DIR are required")

    with psycopg.connect(dsn) as conn:
        raise SystemExit(sync(conn, SOURCES[args.source], Path(root)))
