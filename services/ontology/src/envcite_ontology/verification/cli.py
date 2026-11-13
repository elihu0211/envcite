"""`envcite-verify`：驗證尚未接受的標註，依政策接受或駁回。

本命令只跑確定性閘門，預設政策含模型閘門故不會接受任何陳述（模型閘門用 `envcite-pipeline`）。
僅以確定性閘門接受須明寫 `--require`，且只保證與原文的數字、引用、推論程式一致，不保證語意。
"""

import argparse
import logging

import psycopg

from envcite_ontology.settings import DatabaseSettings, load
from envcite_ontology.verification.pipeline import settle

DEFAULT_REQUIRED = "numbers,references,executable,provisos,roundtrip,independent_test,panel"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-verify")
    parser.add_argument("--require", default=DEFAULT_REQUIRED, help="必要的驗證方法，逗號分隔")
    args = parser.parse_args()
    settings = load(DatabaseSettings)
    required = [m.strip() for m in args.require.split(",") if m.strip()]
    with psycopg.connect(settings.dsn) as conn:
        result = settle(conn, required)
    logging.getLogger(__name__).info(
        "驗證完成：%d 個升為 confirmed，%d 個 rejected，%d 輪（政策：%s）",
        result.confirmed,
        result.rejected,
        result.rounds,
        ",".join(required),
    )
