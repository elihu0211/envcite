"""`canon acquire <來源>`：由 CronJob 執行，每個來源一個。

結束碼決定 Job 是否重試：0 成功（含檔案未變）；1 暫時性失敗（連線、資料庫），可重試；
2 資料不合預期，重試不會改變結果——部署清單的 podFailurePolicy 據此直接判定失敗。
"""

import argparse
import logging

import psycopg

from canon.acquire.engine import InvalidSnapshot, Source, acquire
from canon.law.moj import Moj
from canon.settings import SnapshotSettings, load

#: 新來源在此登記。
SOURCES: dict[str, Source] = {s.name: s for s in (Moj(),)}

EXIT_INVALID = 2


def main() -> None:
    """`canon acquire <來源>`：取得該來源的全量檔並存成快照；資料不合預期以結束碼 2 結束。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="canon acquire")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    try:
        with psycopg.connect(settings.dsn) as conn:
            acquire(SOURCES[args.source], settings.snapshot_dir, conn)
    except InvalidSnapshot as e:
        logging.getLogger(__name__).error("invalid snapshot: %s", e)
        raise SystemExit(EXIT_INVALID) from e
