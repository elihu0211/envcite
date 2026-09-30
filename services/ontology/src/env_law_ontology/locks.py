"""資料庫的諮詢鎖（env-law-docs 03-04 §6）。

CronJob 的 `Forbid` 只擋同一個 CronJob，擋不住手動執行，也擋不住不同來源共用投影。鎖是
連線層級的：連線結束（Job 被殺）自動釋放，不留殘鎖；中間的提交不影響它。拿不到就輪詢
等待，逾時即失敗，由 Job 的重試政策處理。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

#: 等待的秒數；測試以 monkeypatch 縮短。
DEFAULT_TIMEOUT = 600.0
_POLL = 1.0


class LockTimeout(TimeoutError):
    """另一個寫入者持有鎖的時間超過等待上限。"""


@contextmanager
def advisory(
    conn: psycopg.Connection[tuple[object, ...]], key: str, timeout: float | None = None
) -> Iterator[None]:
    wait = DEFAULT_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + wait
    while True:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
        ).fetchone()
        conn.rollback()
        if row is not None and row[0]:
            break
        if time.monotonic() >= deadline:
            raise LockTimeout(f"could not take lock {key!r} within {wait:g}s")
        time.sleep(min(_POLL, max(deadline - time.monotonic(), 0.0)))
    try:
        yield
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
        conn.commit()
