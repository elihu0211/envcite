"""取得引擎：各來源共用的步驟。

來源只回答「從哪裡下載」與「這份檔案裡有什麼」；下載、雜湊、冪等判斷、存檔與寫入
由引擎做，每個來源都一樣。任一步失敗，資料庫不留下任何東西；檔案先寫、資料庫後
提交，檔名即雜湊，所以重跑無害。
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from psycopg import Connection

log = logging.getLogger(__name__)

_CHUNK = 1 << 20


class InvalidSnapshot(ValueError):
    """檔案下載完整，內容卻不合預期。重試不會改變結果。"""


class NotAnArchive(InvalidSnapshot):
    """檔案不是預期的容器格式（zip）。"""


class DownloadFailed(OSError):
    """下載到的不是預期的容器：來源的錯誤頁或中斷的回應，重試可能就好（03-04 §3）。

    與 `InvalidSnapshot` 分開：容器完好而資料形狀不合，才是重試無益的情形。
    """


@dataclass(frozen=True, slots=True)
class Feed:
    """來源的一個全量檔：`source.feed` 的值與下載位址。"""

    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Contents:
    data_date: date
    #: （來源內的鍵，內容的 SHA-256）。
    items: Sequence[tuple[str, bytes]]


class Source(Protocol):
    name: str
    #: 寫入每份快照的出處標示。
    attribution: str
    feeds: Sequence[Feed]

    def tls(self) -> ssl.SSLContext: ...

    def read(self, path: Path) -> Contents:
        """解析已下載的檔案；內容不合預期時拋出 `InvalidSnapshot`。"""
        ...


@dataclass(frozen=True, slots=True)
class Outcome:
    feed: str
    #: 新存入的快照；檔案與上次相同時為 None。
    snapshot_id: int | None


def acquire(source: Source, root: Path, conn: Connection[tuple[object, ...]]) -> list[Outcome]:
    return [_acquire_feed(source, feed, root, conn) for feed in source.feeds]


def _acquire_feed(
    source: Source, feed: Feed, root: Path, conn: Connection[tuple[object, ...]]
) -> Outcome:
    partial = root / ".partial" / feed.name
    partial.parent.mkdir(parents=True, exist_ok=True)
    try:
        digest, size = _download(feed.url, partial, source.tls())
        if _known(conn, feed.name, digest):
            log.info("%s: unchanged (%s)", feed.name, digest.hex()[:12])
            return Outcome(feed.name, None)

        try:
            contents = source.read(partial)
        except NotAnArchive as e:
            raise DownloadFailed(f"{feed.name}: {e}") from e
        keys = [key for key, _ in contents.items]
        if not keys:
            raise InvalidSnapshot(f"{feed.name}: no items")
        if len(set(keys)) != len(keys):
            raise InvalidSnapshot(f"{feed.name}: duplicate item keys")

        # 以內容定址：同一份檔案永遠同一個路徑。
        location = f"{feed.name}/{digest.hex()}"
        (root / location).parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, root / location)
    finally:
        partial.unlink(missing_ok=True)

    snapshot_id = _record(conn, source, feed.name, contents, digest, size, location)
    log.info(
        "%s: stored snapshot %d (%s, %d items, data date %s)",
        feed.name,
        snapshot_id,
        digest.hex()[:12],
        len(contents.items),
        contents.data_date,
    )
    return Outcome(feed.name, snapshot_id)


def _download(url: str, dest: Path, context: ssl.SSLContext) -> tuple[bytes, int]:
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, context=context, timeout=300) as r, dest.open("wb") as f:
        while chunk := r.read(_CHUNK):
            h.update(chunk)
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        raise InvalidSnapshot(f"{url}: empty response")
    return h.digest(), size


def _known(conn: Connection[tuple[object, ...]], feed: str, digest: bytes) -> bool:
    row = conn.execute(
        "SELECT 1 FROM source.snapshot WHERE feed = %s AND sha256 = %s", (feed, digest)
    ).fetchone()
    conn.rollback()
    return row is not None


def _record(
    conn: Connection[tuple[object, ...]],
    source: Source,
    feed: str,
    contents: Contents,
    digest: bytes,
    size: int,
    location: str,
) -> int:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO source.snapshot (feed, data_date, sha256, size, location, license_notice)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (feed, contents.data_date, digest, size, location, source.attribution),
        ).fetchone()
        assert row is not None and isinstance(row[0], int)
        snapshot_id = row[0]
        _record_items(conn, feed, snapshot_id, contents.items)
        # 連續兩份全量快照都未出現：前一份快照之前就再也沒見過。
        conn.execute(
            """
            UPDATE source.item SET dropped_on = %(on)s
             WHERE feed = %(feed)s AND dropped_on IS NULL
               AND last_snapshot_id < (SELECT max(id) FROM source.snapshot
                                        WHERE feed = %(feed)s AND id < %(id)s)
            """,
            {"feed": feed, "id": snapshot_id, "on": contents.data_date},
        )
    return snapshot_id


def _record_items(
    conn: Connection[tuple[object, ...]],
    feed: str,
    snapshot_id: int,
    items: Iterable[tuple[str, bytes]],
) -> None:
    conn.execute("CREATE TEMP TABLE incoming (key text, content_sha256 bytea) ON COMMIT DROP")
    with conn.cursor().copy("COPY incoming (key, content_sha256) FROM STDIN") as copy:
        for key, content in items:
            copy.write_row((key, content))
    conn.execute(
        """
        INSERT INTO source.item (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)
        SELECT %(feed)s, key, content_sha256, %(id)s, %(id)s FROM incoming
        ON CONFLICT (feed, key) DO UPDATE
           SET content_sha256 = excluded.content_sha256,
               last_snapshot_id = excluded.last_snapshot_id,
               dropped_on = NULL
        """,
        {"feed": feed, "id": snapshot_id},
    )
