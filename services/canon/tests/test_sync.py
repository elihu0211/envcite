"""One cycle (02-04 §4): acquire, import, projection, and how each failure exits."""

import zipfile
from pathlib import Path
from typing import Any

import pytest

from canon import sync
from canon.acquire.engine import DownloadFailed, acquire
from canon.acquire.web import Http
from canon.db import Conn
from tests.fake_web import Served
from tests.test_acquire import FEED_URL, LocalMoj, _publish
from tests.test_import import _law


def _http(feed: Path) -> Http:
    return Served({FEED_URL: feed}).http()


def _cycle(tmp_path: Path, conn: Conn, laws: list[dict[str, Any]]) -> int:
    feed = _publish(tmp_path / "feed.zip", "2026/9/18 上午 12:00:00", laws)
    return sync.sync(conn, LocalMoj(), tmp_path / "snapshots", http=_http(feed))


def _count(conn: Conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def test_a_cycle_acquires_imports_and_indexes(tmp_path: Path, conn: Conn) -> None:
    assert _cycle(tmp_path, conn, [_law("T0000001", "噪音管制法")]) == 0

    assert _count(conn, "SELECT count(*) FROM source.snapshot") == 1
    assert (
        _count(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id = 'T0000001'") == 1
    )
    assert _count(conn, "SELECT count(*) FROM retrieval.passage_lexical") > 1


def test_a_cycle_with_nothing_to_do_changes_nothing(tmp_path: Path, conn: Conn) -> None:
    # 同一個檔案：zip 內記著寫入時間，重新打包同樣的內容位元組不同，會被當成新檔。
    feed = _publish(
        tmp_path / "feed.zip", "2026/9/18 上午 12:00:00", [_law("T0000001", "噪音管制法")]
    )
    http = _http(feed)
    sync.sync(conn, LocalMoj(), tmp_path / "snapshots", http=http)
    indexed = _count(conn, "SELECT count(*) FROM retrieval.passage_lexical")

    assert sync.sync(conn, LocalMoj(), tmp_path / "snapshots", http=http) == 0
    assert _count(conn, "SELECT count(*) FROM source.snapshot") == 1
    assert _count(conn, "SELECT count(*) FROM retrieval.passage_lexical") == indexed


def test_a_download_that_is_not_a_zip_is_transient(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    feed.write_text("error page")
    http = _http(feed)
    with pytest.raises(DownloadFailed):
        acquire(LocalMoj(), tmp_path / "snapshots", conn, http=http)

    assert sync.sync(conn, LocalMoj(), tmp_path / "snapshots", http=http) == 1


def test_a_zip_without_laws_is_invalid_and_not_retried(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    with zipfile.ZipFile(feed, "w") as z:
        z.writestr("ChLaw.json", '{"UpdateDate": "2026/9/18", "Other": []}')

    assert sync.sync(conn, LocalMoj(), tmp_path / "snapshots", http=_http(feed)) == 2


def test_a_batch_over_the_limits_is_invalid_and_writes_nothing(tmp_path: Path, conn: Conn) -> None:
    assert _cycle(tmp_path, conn, [_law("T0000001", "壞法", LawLevel="判例")]) == 2
    assert (
        _count(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id = 'T0000001'") == 0
    )
