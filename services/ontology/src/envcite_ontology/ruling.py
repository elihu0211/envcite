"""違憲失效（能力問題 A5）：大法官解釋與憲法法庭判決所宣告失效的條文。

**由人載入，不從文字推斷。** 失效日在解釋文裡是自然語言，來源網站（憲法法庭）只有 HTML，
沒有結構化的欄位；所以每則裁判附解釋文原文與出處，失效日由載入的人讀原文填。
**裁判不可改**：同一字號再載入時內容必須相同，相同則沿用。一次載入一個交易。

文件格式：

    {"rulings": [
       {"ruling_no": "<釋字第N號 或 N年憲判字第M號>", "kind": "interpretation | judgment",
        "decided_on": "<公布或宣判日>", "source_url": "https://cons.judicial.gov.tw/…",
        "excerpt": "<解釋文／主文中宣告失效的原文>",
        "invalidates": [{"law": "<pcode>", "path": "art_7/para_1", "invalid_from": "<失效日>",
                         "partial": false}]}]}

條文單元必須在語料裡；一個條文單元對同一則裁判只記一次。`partial` 可省略（預設否）。

兩個入口：`envcite-ruling load <檔>` 載入上述文件（人讀原文填失效日）；`envcite-ruling fetch`
自憲法法庭網站取得裁判並由規則提出失效（`ruling_sync`），兩者可並存：抽取流程只刪改自己寫的列。
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import psycopg

from envcite_ontology.annotate import AnnotationError, _provision
from envcite_ontology.settings import DatabaseSettings
from envcite_ontology.settings import load as load_settings

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

EXIT_INVALID = 2
EXIT_TRANSIENT = 1


class RulingError(AnnotationError):
    """文件不合：條文單元不在語料、同一字號內容不同、失效日早於裁判日。"""


@dataclass(frozen=True)
class LoadedRulings:
    rulings: int
    #: 已載入過、內容相同而沿用的件數
    repeated: int
    invalidations: int


def load(conn: Conn, doc: Mapping[str, Any]) -> LoadedRulings:
    """寫入一份裁判文件；整份成功或整份不寫入。"""
    try:
        loaded = _write(conn, doc)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("違憲失效載入：%s", loaded)
    return loaded


def _write(conn: Conn, doc: Mapping[str, Any]) -> LoadedRulings:
    try:
        return _rulings(conn, doc)
    except RulingError:
        raise
    except AnnotationError as e:
        raise RulingError(str(e)) from e
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise RulingError(f"文件欄位缺漏或型別不合：{e!r}") from e
    except psycopg.errors.IntegrityError as e:
        raise RulingError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise RulingError(f"值不合欄位型別：{e}") from e
    except psycopg.errors.RaiseException as e:
        raise RulingError(str(e)) from e


def _rulings(conn: Conn, doc: Mapping[str, Any]) -> LoadedRulings:
    count = repeated = invalidations = 0
    for r in doc["rulings"]:
        count += 1
        ruling_id, again = _ruling(conn, r)
        repeated += again
        for i in r["invalidates"]:
            provision_id = _provision(conn, i["law"], i["path"])
            invalidations += conn.execute(
                "INSERT INTO corpus.ruling_invalidation"
                " (ruling_id, resource_id, provision_id, invalid_from, partial)"
                " SELECT %s, resource_id, id, %s, %s FROM corpus.provision WHERE id = %s"
                " ON CONFLICT (ruling_id, provision_id) DO NOTHING",
                (
                    ruling_id,
                    date.fromisoformat(str(i["invalid_from"])),
                    bool(i.get("partial", False)),
                    provision_id,
                ),
            ).rowcount
    return LoadedRulings(count, repeated, invalidations)


def _ruling(conn: Conn, r: Mapping[str, Any]) -> tuple[int, int]:
    """寫入一則裁判；已有同一字號時內容必須相同。回傳（id，是否沿用）。"""
    decided = date.fromisoformat(str(r["decided_on"]))
    row = conn.execute(
        "SELECT id, ruling_kind::text, decided_on, source_url, excerpt"
        " FROM corpus.constitutional_ruling WHERE ruling_no = %s",
        (r["ruling_no"],),
    ).fetchone()
    if row is not None:
        if tuple(row[1:]) != (r["kind"], decided, r["source_url"], r["excerpt"]):
            raise RulingError(f"{r['ruling_no']} 已載入，內容與這次不同")
        return cast(int, row[0]), 1
    new = conn.execute(
        "INSERT INTO corpus.constitutional_ruling"
        " (ruling_no, ruling_kind, decided_on, source_url, excerpt)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (r["ruling_no"], r["kind"], decided, r["source_url"], r["excerpt"]),
    ).fetchone()
    assert new is not None
    return cast(int, new[0]), 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-ruling")
    commands = parser.add_subparsers(dest="command", required=True)
    load_cmd = commands.add_parser("load", help="載入違憲失效裁判文件（JSON）")
    load_cmd.add_argument("file", type=Path)
    fetch_cmd = commands.add_parser("fetch", help="自憲法法庭網站取得裁判並提出失效")
    fetch_cmd.add_argument(
        "--rescan", action="store_true", help="已掃描過的頁面也重抓（網站內容有疑義時）"
    )
    args = parser.parse_args()

    settings = load_settings(DatabaseSettings)
    try:
        with psycopg.connect(settings.dsn) as conn:
            if args.command == "load":
                load(conn, json.loads(args.file.read_text(encoding="utf-8")))
            else:
                raise SystemExit(_fetch(conn, rescan=args.rescan))
    except (RulingError, json.JSONDecodeError) as e:
        log.error("ruling rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e


def _fetch(conn: Conn, *, rescan: bool) -> int:
    from envcite_ontology import ruling_sync

    try:
        result = ruling_sync.sync(conn, rescan=rescan)
    except OSError as e:
        log.error("ruling fetch failed: %s", e)
        return EXIT_TRANSIENT
    return EXIT_INVALID if result.unreadable else 0
