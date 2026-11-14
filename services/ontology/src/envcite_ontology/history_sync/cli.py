"""`envcite-history` 指令列。"""

import argparse
import logging

import psycopg

from envcite_jobkit import (
    Policy,
)
from envcite_ontology.acquire.web import Http
from envcite_ontology.history_sources import SOURCES
from envcite_ontology.history_sync.policy import POLICY
from envcite_ontology.history_sync.run import sync
from envcite_ontology.settings import SnapshotSettings, load
from envcite_ontology.verification import config


def main() -> None:
    """`envcite-history <來源> [步驟]`：取得法規的歷史版本並補進語料。

    步驟 `acquire` 只取得各版存成快照，`import` 只回推並寫入語料，預設的 `sync` 兩者都跑。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-history")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("step", nargs="?", default="sync", choices=("sync", "acquire", "import"))
    parser.add_argument("--rescan", action="store_true", help="過去的版本全部重抓")
    parser.add_argument("--dry-run", action="store_true", help="匯入只回推與驗收，不寫")
    args = parser.parse_args()

    source = SOURCES[args.source]()
    settings = load(SnapshotSettings)
    with psycopg.connect(settings.dsn) as conn:
        embedder = config.embedder_from_settings(settings, timeout=60.0)
        policy = Policy.load(defaults=POLICY)
        raise SystemExit(
            sync(
                conn,
                source,
                settings.snapshot_dir,
                Http(source.pause, legacy_tls=source.legacy_tls),
                step=args.step,
                rescan=args.rescan,
                dry_run=args.dry_run,
                embedder=embedder,
                policy=policy,
            )
        )
