"""`env-law-import <來源> [--dry-run]`：取得之後的另一個步驟（03-03 §3）。

結束碼同 `env-law-acquire`：0 成功；1 暫時性失敗，可重試；2 整批比例越線，重試無益。
`--dry-run` 只解析與驗收；寫入後重建檢索投影。
"""

from __future__ import annotations

import argparse
import logging

import psycopg

from env_law_ontology.acquire.cli import EXIT_INVALID, SOURCES
from env_law_ontology.acquire.engine import InvalidSnapshot
from env_law_ontology.ingest.importer import BatchRejected, run
from env_law_ontology.projection import rebuild
from env_law_ontology.settings import SnapshotSettings, load


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-import")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    try:
        with psycopg.connect(settings.ontology_dsn) as conn:
            run(conn, settings.acquire_snapshot_dir, SOURCES[args.source], dry_run=args.dry_run)
            if not args.dry_run:
                rebuild(conn)
    except (BatchRejected, InvalidSnapshot) as e:
        logging.getLogger(__name__).error("import rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
