"""`env-law-import <來源> [--dry-run]`：取得之後的另一個步驟（03-03 §3）。

結束碼同 `env-law-acquire`：0 成功；1 暫時性失敗（資料庫），可重試；2 整批比例越線，
重試不會改變結果。`--dry-run` 只解析與驗收，不寫入。寫入之後接著補齊檢索投影。
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import psycopg

from env_law_ontology.acquire.cli import EXIT_INVALID, SOURCES
from env_law_ontology.acquire.engine import InvalidSnapshot
from env_law_ontology.ingest.importer import BatchRejected, run
from env_law_ontology.projection import rebuild


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-import")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("ONTOLOGY_DSN", "")
    root = os.environ.get("ACQUIRE_SNAPSHOT_DIR", "")
    if not dsn or not root:
        raise SystemExit("ONTOLOGY_DSN and ACQUIRE_SNAPSHOT_DIR are required")

    try:
        with psycopg.connect(dsn) as conn:
            run(conn, Path(root), SOURCES[args.source], dry_run=args.dry_run)
            if not args.dry_run:
                rebuild(conn)
    except (BatchRejected, InvalidSnapshot) as e:
        logging.getLogger(__name__).error("import rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
