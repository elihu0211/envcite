"""取得（第一步）：列出目標，逐部抓各版存成內容定址的快照。"""

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import NamedTuple

from envcite_jobkit import (
    DeadLetter,
    ExitCode,
    ItemError,
    JsonlDeadLetterSink,
    MemoryDeadLetterSink,
    Policy,
    RunStats,
    run,
)
from envcite_ontology.clock import Today
from envcite_ontology.db import Conn, fetch_one, scalar
from envcite_ontology.history_sources import HistorySource, Target, Web
from envcite_ontology.history_sync.policy import COVERAGE_TOLERANCE, POLICY
from envcite_ontology.history_sync.report import Report
from envcite_ontology.ingest.backfill.models import UnreadablePage

log = logging.getLogger(__name__)


class _LoggedSink:
    """隔離時記一行日誌（哪一部、為什麼），再交給政策指定的去處。"""

    def __init__(self, policy: Policy) -> None:
        self._sink: JsonlDeadLetterSink | MemoryDeadLetterSink = (
            JsonlDeadLetterSink(policy.dead_letter_path)
            if policy.dead_letter_path
            else MemoryDeadLetterSink()
        )

    def record(self, entry: DeadLetter) -> None:
        """記一行警告（哪一部、為什麼），再交給底層的去處。"""
        log.warning("隔離 %s（%s）：%s", entry.item_id, entry.kind, entry.error)
        self._sink.record(entry)


def acquire(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    report: Report,
    *,
    rescan: bool = False,
    policy: Policy = POLICY,
    sleep: Callable[[float], None] = time.sleep,
    today: Today = date.today,
) -> ExitCode:
    """取得（第一步）：列出目標，逐部抓各版存成快照；回傳結束碼。

    隔離的部數超過列舉數的 1% 回 `ExitCode.FAILED`。
    """
    listed = source.targets(conn, web)
    conn.rollback()
    report.listed = len(listed)
    stats = RunStats()
    code = run(
        listed,
        lambda t: _acquire_one(conn, source, root, web, t, report, rescan=rescan, today=today()),
        policy,
        item_id=lambda t: t.key,
        sink=_LoggedSink(policy),
        stats=stats,
        sleep=sleep,
    )
    report.dead_lettered = stats.dead_lettered
    if code is ExitCode.OK and stats.dead_lettered > COVERAGE_TOLERANCE * max(len(listed), 1):
        log.error("隔離 %d 部，超過列舉數 %d 的 1%%", stats.dead_lettered, len(listed))
        return ExitCode.FAILED
    return code


def _acquire_one(
    conn: Conn,
    source: HistorySource,
    root: Path,
    web: Web,
    target: Target,
    report: Report,
    *,
    rescan: bool,
    today: date,
) -> None:
    previous: str | None = None
    if not rescan:
        previous = _latest_snapshot(conn, source, root, target.key)
        conn.rollback()
    try:
        fetched = source.fetch(web, target, previous)
    except UnreadablePage as e:
        raise ItemError(f"{target.key}: {e}") from e
    report.fetched_pages += fetched.fetched
    report.reused_pages += fetched.reused
    data = fetched.snapshot.encode()
    digest = hashlib.sha256(data).digest()
    location = f"{source.feed}/{digest.hex()}"
    path = root / location
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    with conn.transaction():
        known = conn.execute(
            "SELECT id FROM source.snapshot WHERE feed = %s AND sha256 = %s",
            (source.feed, digest),
        ).fetchone()
        if known is not None:
            return
        snapshot = scalar(
            conn,
            "INSERT INTO source.snapshot (feed, data_date, sha256, size_bytes, location,"
            "  license_notice) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (source.feed, today, digest, len(data), location, source.attribution),
            int,
        )
        conn.execute(
            "INSERT INTO source.item"
            " (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
            " VALUES (%(feed)s, %(key)s, %(sha)s, %(id)s, %(id)s)"
            " ON CONFLICT (feed, key) DO UPDATE SET content_sha256 = excluded.content_sha256,"
            "   last_snapshot_id = excluded.last_snapshot_id, dropped_on = NULL",
            {"feed": source.feed, "key": target.key, "sha": digest, "id": snapshot},
        )
        report.snapshots += 1


class _LocationRow(NamedTuple):
    location: str


def _latest_snapshot(conn: Conn, source: HistorySource, root: Path, key: str) -> str | None:
    """這部法規上一次的快照文字；沒有（或檔案不在）為 None。"""
    row = fetch_one(
        conn,
        "SELECT s.location FROM source.item AS i"
        "  JOIN source.snapshot AS s ON s.id = i.last_snapshot_id"
        " WHERE i.feed = %s AND i.key = %s",
        (source.feed, key),
        _LocationRow,
    )
    if row is None:
        return None
    path = root / row.location
    return path.read_text() if path.exists() else None
