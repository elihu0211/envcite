"""The acquisition engine, driven through the real MOJ reader.

Files are served over `file://`, so the engine downloads, hashes and stores them
exactly as it would from the API, without the network.
"""

import json
import zipfile
from datetime import date
from pathlib import Path

import psycopg
import pytest

from env_law_ontology.acquire.engine import Feed, InvalidSnapshot, Outcome, acquire
from env_law_ontology.acquire.moj import Moj

Conn = psycopg.Connection[tuple[object, ...]]


class LocalMoj(Moj):
    """The MOJ source, reading its law feed from a local file."""

    def __init__(self, path: Path) -> None:
        self.feeds = (Feed("moj_law", path.as_uri()),)


def _law(pcode: str, name: str = "甲法") -> dict[str, object]:
    return {"LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}", "LawName": name}


def _publish(path: Path, update: str, laws: list[dict[str, object]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "ChLaw.json", json.dumps({"UpdateDate": update, "Laws": laws}, ensure_ascii=False)
        )
        z.writestr("manifest.csv", "name,schema,description\n")
    return path


def _run(tmp_path: Path, conn: Conn, update: str, laws: list[dict[str, object]]) -> Outcome:
    source = LocalMoj(_publish(tmp_path / "feed.zip", update, laws))
    (outcome,) = acquire(source, tmp_path / "snapshots", conn)
    return outcome


def _items(conn: Conn) -> dict[str, tuple[object, object]]:
    """Key → (content hash, withdrawal date)."""
    rows = conn.execute("SELECT key, content_sha256, withdrawn_on FROM source.item").fetchall()
    conn.rollback()
    return {str(k): (h, w) for k, h, w in rows}


def test_a_new_file_is_stored_and_its_items_recorded(tmp_path: Path, conn: Conn) -> None:
    outcome = _run(tmp_path, conn, "2026/9/18 上午 12:00:00", [_law("T0000001"), _law("T0000002")])

    assert outcome.snapshot_id is not None
    row = conn.execute(
        "SELECT data_date, location, attribution FROM source.snapshot WHERE id = %s",
        (outcome.snapshot_id,),
    ).fetchone()
    assert row is not None
    data_date, location, attribution = row
    assert data_date == date(2026, 9, 18)
    assert (tmp_path / "snapshots" / str(location)).read_bytes() == (
        tmp_path / "feed.zip"
    ).read_bytes()
    assert "全國法規資料庫" in str(attribution)
    assert sorted(_items(conn)) == ["T0000001", "T0000002"]


def test_the_same_file_twice_is_a_no_op(tmp_path: Path, conn: Conn) -> None:
    laws = [_law("T0000001")]
    _run(tmp_path, conn, "2026/9/18 上午 12:00:00", laws)

    again = _run(tmp_path, conn, "2026/9/18 上午 12:00:00", laws)

    assert again.snapshot_id is None
    assert conn.execute("SELECT count(*) FROM source.snapshot").fetchone() == (1,)


def test_a_changed_law_gets_a_new_content_hash(tmp_path: Path, conn: Conn) -> None:
    _run(tmp_path, conn, "2026/9/18 上午 12:00:00", [_law("T0000001", "甲法")])
    before = _items(conn)["T0000001"][0]

    _run(tmp_path, conn, "2026/9/25 上午 12:00:00", [_law("T0000001", "甲法（修正）")])

    assert _items(conn)["T0000001"][0] != before


def test_a_law_missing_from_two_consecutive_snapshots_is_withdrawn(
    tmp_path: Path, conn: Conn
) -> None:
    _run(tmp_path, conn, "2026/9/1 上午 12:00:00", [_law("T0000001"), _law("T0000002")])
    _run(tmp_path, conn, "2026/9/2 上午 12:00:00", [_law("T0000001")])
    assert _items(conn)["T0000002"][1] is None  # one miss is not enough

    _run(tmp_path, conn, "2026/9/3 上午 12:00:00", [_law("T0000001", "改")])
    assert _items(conn)["T0000002"][1] == date(2026, 9, 3)

    _run(tmp_path, conn, "2026/9/4 上午 12:00:00", [_law("T0000001", "改"), _law("T0000002")])
    assert _items(conn)["T0000002"][1] is None  # back again


@pytest.mark.parametrize(
    "update, laws",
    [
        ("2026/9/18 上午 12:00:00", [{"LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx"}]),
        ("not a date", [_law("T0000001")]),
        ("2026/9/18 上午 12:00:00", [_law("T0000001"), _law("T0000001")]),
        ("2026/9/18 上午 12:00:00", []),
    ],
    ids=["no-pcode", "bad-date", "duplicate", "empty"],
)
def test_an_invalid_file_leaves_nothing_behind(
    tmp_path: Path, conn: Conn, update: str, laws: list[dict[str, object]]
) -> None:
    with pytest.raises(InvalidSnapshot):
        _run(tmp_path, conn, update, laws)

    assert conn.execute("SELECT count(*) FROM source.snapshot").fetchone() == (0,)
    assert not any(p.is_file() for p in (tmp_path / "snapshots").rglob("*"))
