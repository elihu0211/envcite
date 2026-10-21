"""`envcite-sync <來源>`：取得、匯入、檢索投影依序執行。

三步冪等，每次全跑（映像升級後解析器版本改變，沒有新檔也須重匯）；任一步失敗即停。
結束碼：0 成功（含無事可做）；1 暫時性失敗，可重試；2 重試無益（資料不合預期、整批比例越線）。
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import psycopg

from envcite_ontology import embedding, projection
from envcite_ontology.acquire.cli import EXIT_INVALID, SOURCES
from envcite_ontology.acquire.engine import InvalidSnapshot, Source, acquire
from envcite_ontology.embedding import Embedder
from envcite_ontology.ingest import importer
from envcite_ontology.ingest.importer import BatchRejected
from envcite_ontology.settings import SnapshotSettings, load

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

EXIT_TRANSIENT = 1


def exit_code(error: BaseException) -> int:
    """資料不合預期或整批越線為 2（重試無益），其餘為 1。"""
    return EXIT_INVALID if isinstance(error, InvalidSnapshot | BatchRejected) else EXIT_TRANSIENT


def sync(conn: Conn, source: Source, root: Path, embedder: Embedder | None = None) -> int:
    """跑一個週期；失敗時 rollback 並回傳結束碼。"""
    try:
        acquire(source, root, conn)
        importer.run(conn, root, source)
        projection.rebuild(conn, embedder)
    except Exception as e:
        conn.rollback()
        code = exit_code(e)
        log.error("sync failed (exit %d): %s", code, e)
        return code
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-sync")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    with psycopg.connect(settings.ontology_dsn) as conn:
        embedder = embedding.from_settings(settings, timeout=60.0, max_retries=3)
        raise SystemExit(sync(conn, SOURCES[args.source], settings.acquire_snapshot_dir, embedder))
