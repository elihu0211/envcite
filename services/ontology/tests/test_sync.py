"""One cycle (03-04 §3): acquire, import, projection, and how each failure exits."""

import zipfile
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology import sync
from env_law_ontology.acquire.engine import DownloadFailed, Feed, InvalidSnapshot, acquire
from env_law_ontology.acquire.moj import Moj
from env_law_ontology.ingest.importer import BatchRejected
from tests.test_acquire import _publish
from tests.test_import import _law

Conn = psycopg.Connection[tuple[object, ...]]


class LocalMoj(Moj):
    """The MOJ source over a local file, like the acquisition tests."""

    def __init__(self, path: Path) -> None:
        self.feeds = (Feed("moj_law", path.as_uri()),)


def _cycle(tmp_path: Path, conn: Conn, laws: list[dict[str, Any]]) -> int:
    source = LocalMoj(_publish(tmp_path / "feed.zip", "2026/9/18 上午 12:00:00", laws))
    return sync.sync(conn, source, tmp_path / "snapshots")


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
    assert _count(conn, "SELECT count(*) FROM retrieval.lexical_index") > 1


def test_a_cycle_with_nothing_to_do_changes_nothing(tmp_path: Path, conn: Conn) -> None:
    # 同一個檔案：zip 內記著寫入時間，重新打包同樣的內容位元組不同，會被當成新檔。
    source = LocalMoj(
        _publish(tmp_path / "feed.zip", "2026/9/18 上午 12:00:00", [_law("T0000001", "噪音管制法")])
    )
    sync.sync(conn, source, tmp_path / "snapshots")
    indexed = _count(conn, "SELECT count(*) FROM retrieval.lexical_index")

    assert sync.sync(conn, source, tmp_path / "snapshots") == 0
    assert _count(conn, "SELECT count(*) FROM source.snapshot") == 1
    assert _count(conn, "SELECT count(*) FROM retrieval.lexical_index") == indexed


def test_a_download_that_is_not_a_zip_is_transient(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    feed.write_text("error page")
    source = LocalMoj(feed)
    with pytest.raises(DownloadFailed):
        acquire(source, tmp_path / "snapshots", conn)

    assert sync.sync(conn, source, tmp_path / "snapshots") == 1


def test_a_zip_without_laws_is_invalid_and_not_retried(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    with zipfile.ZipFile(feed, "w") as z:
        z.writestr("ChLaw.json", '{"UpdateDate": "2026/9/18", "Other": []}')

    assert sync.sync(conn, LocalMoj(feed), tmp_path / "snapshots") == 2


def test_a_batch_over_the_limits_is_invalid_and_writes_nothing(tmp_path: Path, conn: Conn) -> None:
    assert _cycle(tmp_path, conn, [_law("T0000001", "壞法", LawLevel="判例")]) == 2
    assert (
        _count(conn, "SELECT count(*) FROM corpus.legal_resource WHERE local_id = 'T0000001'") == 0
    )


def test_exit_codes_separate_what_a_retry_can_fix() -> None:
    assert sync.exit_code(InvalidSnapshot("x")) == 2
    assert sync.exit_code(BatchRejected("x")) == 2
    assert sync.exit_code(DownloadFailed("x")) == 1
    assert sync.exit_code(psycopg.OperationalError("x")) == 1
