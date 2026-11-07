"""`envcite-acquire <來源>`：由 CronJob 執行，每個來源一個。

結束碼決定 Job 是否重試：0 成功（含檔案未變）；1 暫時性失敗（連線、資料庫），可重試；
2 資料不合預期，重試不會改變結果——部署清單的 podFailurePolicy 據此直接判定失敗。
"""

from __future__ import annotations

import argparse
import logging

import psycopg

from envcite_ontology.acquire.engine import InvalidSnapshot, Source, acquire
from envcite_ontology.acquire.local_glrs import LocalGlrs
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.settings import SnapshotSettings, load

#: 新來源在此登記。
SOURCES: dict[str, Source] = {s.name: s for s in (Moj(), LocalGlrs())}

EXIT_INVALID = 2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-acquire")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    settings = load(SnapshotSettings)

    try:
        with psycopg.connect(settings.dsn) as conn:
            acquire(SOURCES[args.source], settings.snapshot_dir, conn)
    except InvalidSnapshot as e:
        logging.getLogger(__name__).error("invalid snapshot: %s", e)
        raise SystemExit(EXIT_INVALID) from e
