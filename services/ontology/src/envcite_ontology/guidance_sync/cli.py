"""`envcite-guidance` 指令列：`load` 載入人寫的文件，`sync` 自公報或環境部取得。"""

import argparse
import json
import logging
from datetime import date
from pathlib import Path

import psycopg

from envcite_jobkit import Policy
from envcite_ontology import guidance
from envcite_ontology.guidance_sync.run import POLICY, sync
from envcite_ontology.settings import DatabaseSettings, SnapshotSettings
from envcite_ontology.settings import load as load_settings

EXIT_INVALID = 2


log = logging.getLogger(__name__)


def main() -> None:
    """`envcite-guidance`：`load` 載入人寫的文件，`sync` 自公報或環境部取得。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-guidance")
    commands = parser.add_subparsers(dest="command", required=True)
    load_cmd = commands.add_parser("load", help="載入一份人寫的行政規則文件（JSON）")
    load_cmd.add_argument("file", type=Path)
    sync_cmd = commands.add_parser("sync", help="自公報或環境部函釋取得行政規則並寫入")
    sync_cmd.add_argument("source", choices=("gazette", "moenv"))
    sync_cmd.add_argument(
        "--since", type=date.fromisoformat, help="只取此日（含）以後的刊登或發文；YYYY-MM-DD"
    )
    sync_cmd.add_argument(
        "--rescan", action="store_true", help="已處理過的也重抓，用來發現網站內容的改變"
    )
    args = parser.parse_args()

    if args.command == "sync":
        raise SystemExit(_run(args.source, since=args.since, rescan=args.rescan))
    settings = load_settings(DatabaseSettings)
    try:
        doc = json.loads(args.file.read_text(encoding="utf-8"))
        with psycopg.connect(settings.dsn) as conn:
            guidance.load(conn, doc)
    except (guidance.GuidanceError, json.JSONDecodeError) as e:
        log.error("guidance rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e


def _run(source: str, *, since: date | None, rescan: bool) -> int:
    settings = load_settings(SnapshotSettings)
    policy = Policy.load(defaults=POLICY)
    with psycopg.connect(settings.dsn) as conn:
        report = sync(
            conn, source, settings.snapshot_dir, since=since, rescan=rescan, policy=policy
        )
    return report.exit_code
