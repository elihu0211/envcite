"""One scheduled cycle (03-04 §3): acquire, import, projection, and the record it leaves."""

import zipfile
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology import locks, sync
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


def _run(conn: Conn) -> dict[str, Any]:
    cur = conn.execute("SELECT * FROM source.run ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    assert row is not None and cur.description is not None
    return {c.name: v for c, v in zip(cur.description, row, strict=True)}


def test_a_cycle_acquires_imports_indexes_and_records_itself(tmp_path: Path, conn: Conn) -> None:
    assert _cycle(tmp_path, conn, [_law("T0000001", "噪音管制法")]) == 0

    run = _run(conn)
    assert (run["outcome"], run["exit_code"], run["step"]) == ("success", 0, "projection")
    assert (run["snapshots_new"], run["laws_added"], run["laws_rejected"]) == (1, 1, 0)
    assert run["articles_indexed"] > 1 and run["finished_at"] is not None
    assert set(run["ratios"]) == {
        "rejected_laws",
        "degraded_articles",
        "unknown_events",
        "unconfirmed_articles",
    }
    # 條文單元版本連回寫入它的那次執行
    row = conn.execute(
        "SELECT count(*) FROM corpus.provision_version WHERE run_id = %s", (run["id"],)
    ).fetchone()
    assert row is not None and int(row[0]) >= 2  # type: ignore[call-overload]


def test_a_cycle_with_nothing_to_do_says_so(tmp_path: Path, conn: Conn) -> None:
    laws = [_law("T0000001", "噪音管制法")]
    _cycle(tmp_path, conn, laws)
    assert _cycle(tmp_path, conn, laws) == 0

    run = _run(conn)
    assert run["outcome"] == "unchanged" and run["snapshots_new"] == 0
    assert run["laws_added"] == 0 and run["articles_indexed"] == 0


def test_a_download_that_is_not_a_zip_is_transient(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    feed.write_text("error page")
    source = LocalMoj(feed)
    with pytest.raises(DownloadFailed):
        acquire(source, tmp_path / "snapshots", conn)

    assert sync.sync(conn, source, tmp_path / "snapshots") == 1
    run = _run(conn)
    assert (run["outcome"], run["exit_code"], run["step"]) == ("failed", 1, "acquire")


def test_a_zip_without_laws_is_invalid_and_not_retried(tmp_path: Path, conn: Conn) -> None:
    feed = tmp_path / "feed.zip"
    with zipfile.ZipFile(feed, "w") as z:
        z.writestr("ChLaw.json", '{"UpdateDate": "2026/9/18", "Other": []}')

    assert sync.sync(conn, LocalMoj(feed), tmp_path / "snapshots") == 2
    assert _run(conn)["exit_code"] == 2


def test_a_batch_over_the_limits_stops_at_the_import_step(tmp_path: Path, conn: Conn) -> None:
    assert _cycle(tmp_path, conn, [_law("T0000001", "壞法", LawLevel="判例")]) == 2

    run = _run(conn)
    assert (run["outcome"], run["exit_code"], run["step"]) == ("rejected", 2, "import")


def test_a_held_lock_fails_the_cycle_as_transient(
    tmp_path: Path, conn: Conn, dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks, "DEFAULT_TIMEOUT", 0.3)
    with psycopg.connect(dsn) as other, locks.advisory(other, "import:moj"):
        assert _cycle(tmp_path, conn, [_law("T0000001", "噪音管制法")]) == 1

    run = _run(conn)
    assert (run["outcome"], run["step"]) == ("failed", "import")


def test_exit_codes_separate_what_a_retry_can_fix() -> None:
    assert sync.exit_code(InvalidSnapshot("x")) == 2
    assert sync.exit_code(BatchRejected("x")) == 2
    assert sync.exit_code(DownloadFailed("x")) == 1
    assert sync.exit_code(locks.LockTimeout("x")) == 1
    assert sync.exit_code(psycopg.OperationalError("x")) == 1


def test_a_lock_is_exclusive_and_released_on_exit(dsn: str) -> None:
    with psycopg.connect(dsn) as a, psycopg.connect(dsn) as b:
        with (
            locks.advisory(a, "k"),
            pytest.raises(locks.LockTimeout),
            locks.advisory(b, "k", timeout=0.2),
        ):
            pass
        with locks.advisory(b, "k", timeout=0.2):
            pass
