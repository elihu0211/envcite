"""`env-law-verify`：驗證尚未被接受的標註，依政策接受或駁回。沒有人在這條線上。

預設政策要求的方法含需要呼叫模型的閘門（往返驗證、獨立測試、跨家族評審）：本命令只跑確定性閘門，
所以用預設政策不會接受任何陳述；要跑模型閘門用 `env-law-pipeline`。
只用確定性閘門接受，要明說（`--require numbers,references,executable`），
並知道這樣接受的只是「與原文的數字、引用、與推論程式一致」，不是「語意忠於原文」。
"""

from __future__ import annotations

import argparse
import logging
import os

import psycopg

from env_law_ontology.verification.pipeline import settle

DEFAULT_REQUIRED = "numbers,references,executable,roundtrip,independent_test,panel"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-verify")
    parser.add_argument("--require", default=DEFAULT_REQUIRED, help="必要的驗證方法，逗號分隔")
    args = parser.parse_args()
    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")
    required = [m.strip() for m in args.require.split(",") if m.strip()]
    with psycopg.connect(dsn) as conn:
        result = settle(conn, required)
    logging.getLogger(__name__).info(
        "驗證完成：%d 個升為 confirmed，%d 個 rejected，%d 輪（政策：%s）",
        result.confirmed,
        result.rejected,
        result.rounds,
        ",".join(required),
    )
