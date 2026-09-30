"""`env-law-sync <來源>`：由 CronJob 執行，取得、匯入、檢索投影依序做完（env-law-docs 03-04 §3）。

三步各自冪等：沒有事可做時只花數秒，所以每個週期都全部執行，不以「取得有新檔」為條件——
映像升級之後解析器版本改變，沒有新檔也必須重匯。任一步失敗，後面的不執行。

結束碼決定 Job 是否重試：0 成功（含無事可做）；1 暫時性失敗，可重試；2 重試不會改變
結果——資料不合預期或整批比例越線。每次執行寫一列 `source.run`，記到達哪一步與結果。
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from env_law_ontology import projection
from env_law_ontology.acquire.cli import EXIT_INVALID, SOURCES
from env_law_ontology.acquire.engine import InvalidSnapshot, Source, acquire
from env_law_ontology.ingest import importer
from env_law_ontology.ingest.audit import LIMITS
from env_law_ontology.ingest.importer import BatchRejected

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

EXIT_TRANSIENT = 1


@dataclass
class Result:
    snapshots_new: int = 0
    actions: dict[str, int] = field(default_factory=dict)
    articles_indexed: int = 0
    ratios: dict[str, float] = field(default_factory=dict)

    @property
    def unchanged(self) -> bool:
        changed = ("added", "versioned", "corrected", "refreshed")
        return (
            self.snapshots_new == 0
            and self.articles_indexed == 0
            and not any(self.actions.get(a) for a in changed)
        )


def exit_code(error: BaseException) -> int:
    """重試無益者是 2：資料不合預期，或整批比例越線。其餘（連線、資料庫、鎖逾時）是 1。"""
    return EXIT_INVALID if isinstance(error, InvalidSnapshot | BatchRejected) else EXIT_TRANSIENT


def sync(conn: Conn, source: Source, root: Path) -> int:
    """執行一個週期；回傳結束碼。`source.run` 的那一列在成功與失敗時都會寫完。"""
    row = conn.execute(
        "INSERT INTO source.run (source) VALUES (%s) RETURNING id", (source.name,)
    ).fetchone()
    conn.commit()
    assert row is not None and isinstance(row[0], int)
    run_id = row[0]
    result = Result()
    try:
        outcomes = acquire(source, root, conn)
        result.snapshots_new = sum(o.snapshot_id is not None for o in outcomes)
        _step(conn, run_id, "import")

        imported = importer.run(conn, root, source, run_id=run_id)
        result.actions = dict(imported.actions)
        result.ratios = {name: imported.metrics.ratio(name) for name in LIMITS}
        _step(conn, run_id, "projection")

        result.articles_indexed = projection.rebuild(conn)
    except BaseException as e:
        conn.rollback()
        code = exit_code(e)
        outcome = "rejected" if isinstance(e, BatchRejected) else "failed"
        _finish(conn, run_id, result, outcome, code)
        log.error("sync failed (exit %d): %s", code, e)
        return code
    _finish(conn, run_id, result, "unchanged" if result.unchanged else "success", 0)
    return 0


def _step(conn: Conn, run_id: int, step: str) -> None:
    conn.execute("UPDATE source.run SET step = %s WHERE id = %s", (step, run_id))
    conn.commit()


def _finish(conn: Conn, run_id: int, r: Result, outcome: str, code: int) -> None:
    a = r.actions
    conn.execute(
        "UPDATE source.run SET finished_at = now(), outcome = %s, exit_code = %s,"
        " snapshots_new = %s, laws_added = %s, laws_versioned = %s, laws_corrected = %s,"
        " laws_refreshed = %s, laws_rejected = %s, articles_indexed = %s, ratios = %s"
        " WHERE id = %s",
        (
            outcome,
            code,
            r.snapshots_new,
            a.get("added", 0),
            a.get("versioned", 0),
            a.get("corrected", 0),
            a.get("refreshed", 0),
            a.get("rejected", 0) + a.get("regressed", 0),
            r.articles_indexed,
            Jsonb(r.ratios),
            run_id,
        ),
    )
    conn.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="env-law-sync")
    parser.add_argument("source", choices=sorted(SOURCES))
    args = parser.parse_args()

    dsn = os.environ.get("ONTOLOGY_DSN", "")
    root = os.environ.get("ACQUIRE_SNAPSHOT_DIR", "")
    if not dsn or not root:
        raise SystemExit("ONTOLOGY_DSN and ACQUIRE_SNAPSHOT_DIR are required")

    with psycopg.connect(dsn) as conn:
        raise SystemExit(sync(conn, SOURCES[args.source], Path(root)))
