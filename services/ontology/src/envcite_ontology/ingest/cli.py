"""`envcite-import <來源> [--dry-run]`：取得之後的另一個步驟（02-03 §3）。

結束碼同 `envcite-acquire`：0 成功；1 暫時性失敗，可重試；2 整批比例越線，重試無益。
`--dry-run` 只解析與驗收；寫入後重建檢索投影。
"""

import argparse
import logging

import psycopg

from envcite_ontology.acquire.cli import EXIT_INVALID, SOURCES
from envcite_ontology.acquire.engine import InvalidSnapshot
from envcite_ontology.ingest.importer import BatchRejected, run
from envcite_ontology.projection import rebuild
from envcite_ontology.settings import SnapshotSettings, load


def main() -> None:
    """`envcite-import <來源> [--dry-run]`：解析來源的最新快照並寫入語料，寫完重建檢索投影。

    `--dry-run` 只解析與驗收，不寫入也不重建。整批比例越線或快照不合時記錄原因，以結束碼 2 退出。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-import")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    try:
        with psycopg.connect(settings.dsn) as conn:
            run(conn, settings.snapshot_dir, SOURCES[args.source], dry_run=args.dry_run)
            if not args.dry_run:
                rebuild(conn)
    except (BatchRejected, InvalidSnapshot) as e:
        logging.getLogger(__name__).error("import rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
