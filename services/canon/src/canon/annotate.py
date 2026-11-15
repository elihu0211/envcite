"""`canon annotate`：讀一份人寫的標註 JSON，交給 `claims.load`。

寫入、錨定與文件格式在 `claims`。這裡只做入口，並把載入器的公開名稱留在原處，
既有的 `from canon.annotate import load` 不用改。
"""

import argparse
import json
import logging
from pathlib import Path

import psycopg

from canon.claims.anchoring import anchor
from canon.claims.enums import LEGAL_EFFECTS, LIABILITY_BASES, TRIGGER_KINDS
from canon.claims.errors import AnnotationError
from canon.claims.loader import Loaded, load
from canon.settings import DatabaseSettings
from canon.settings import load as load_settings

__all__ = [
    "LEGAL_EFFECTS",
    "LIABILITY_BASES",
    "TRIGGER_KINDS",
    "AnnotationError",
    "Loaded",
    "anchor",
    "load",
]

EXIT_INVALID = 2


def main() -> None:
    """`canon annotate <file>`：讀一份標註 JSON，交給 `claims.load` 寫入資料庫。

    文件不合（`AnnotationError`）或不是 JSON 時記錄原因，以結束碼 2 退出，不寫入。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="canon annotate")
    parser.add_argument("file", type=Path, help="標註文件（JSON）")
    args = parser.parse_args()

    settings = load_settings(DatabaseSettings)
    try:
        doc = json.loads(args.file.read_text(encoding="utf-8"))
        with psycopg.connect(settings.dsn) as conn:
            load(conn, doc)
    except (AnnotationError, json.JSONDecodeError) as e:
        logging.getLogger(__name__).error("annotation rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
