"""What each database role may do: annotations are append-only for writers, verification
records and review states change only through functions, and records name the login."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from envcite_ontology.annotate import load
from envcite_ontology.verification.link import link_penalties
from envcite_ontology.verification.pipeline import settle
from tests.test_annotation import _doc, _duty
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1
from tests.verification_support import DETERMINISTIC, Conn, _two_statements, _water_draft

READER = "envcite_ontology_reader"
WRITER = "envcite_ontology_writer"
REVIEWER = "envcite_ontology_reviewer"


@contextmanager
def _as(conn: Conn, role: str) -> Iterator[None]:
    conn.execute(f"SET ROLE {role}")
    try:
        yield
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")


def test_a_reader_reads_and_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    with _as(conn, READER):
        assert _scalar(conn, "SELECT count(*) FROM corpus.legal_resource") == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO vocab.concept_scheme (code, title) VALUES ('x', 'x')")


def test_a_writer_runs_the_pipeline_without_touching_states_directly(
    conn: Conn, sample: None
) -> None:
    with _as(conn, WRITER):
        _water_draft(conn, sample)
        assert settle(conn, DETERMINISTIC).confirmed > 0
        assert link_penalties(conn, None).statements == 10
        assert settle(conn, DETERMINISTIC).confirmed > 0
        assert (
            _scalar(
                conn,
                "SELECT count(*) FROM annotation.verification"
                " WHERE method = 'policy' AND verifier = 'settle' AND recorded_by <> session_user",
            )
            == 0
        )


def test_a_writer_cannot_change_a_state_or_write_a_record(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    attempts = [
        ("UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = %s", (duty,)),
        (
            "INSERT INTO annotation.verification (assertion_id, method, verifier, result)"
            " VALUES (%s, 'policy', 'settle', 'pass')",
            (duty,),
        ),
        ("SELECT annotation.set_confirm_state(%s, 'confirmed', 'looks right')", (duty,)),
        ("SELECT annotation.record_verification(%s, 'policy', 'settle', 'pass')", (duty,)),
    ]
    for sql, args in attempts:
        with _as(conn, WRITER), pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql, args)


def test_a_claim_cannot_be_edited_or_deleted_once_written(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    attempts = [
        "UPDATE annotation.statement_condition SET threshold = 1 WHERE statement_id = %s",
        "DELETE FROM annotation.statement_condition WHERE statement_id = %s",
        "DELETE FROM annotation.statement WHERE id = %s",
        "DELETE FROM annotation.assertion WHERE id = %s",
    ]
    for sql in attempts:
        with _as(conn, WRITER), pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql, (duty,))


def test_only_a_reviewer_loads_reviewed_claims_or_reviews_by_hand(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    with _as(conn, WRITER), pytest.raises(psycopg.errors.InsufficientPrivilege):
        load(conn, _doc(_duty()))  # review: confirmed
    with _as(conn, REVIEWER):
        duty = load(conn, _doc(_duty())).ids["duty"]
        conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', '重新標註')", (duty,))
        conn.commit()
    assert _scalar(conn, "SELECT confirm_state::text FROM annotation.assertion") == "rejected"


def test_a_removed_provision_still_takes_its_claims_along(conn: Conn, tmp_path: Path) -> None:
    """寫入角色不能刪主張，但重寫法規時條文連帶刪除的標註，主張要跟著走，不留孤兒。"""
    _two_statements(conn, tmp_path)
    with _as(conn, WRITER):
        conn.execute("DELETE FROM corpus.provision")
        assert _scalar(conn, "SELECT count(*) FROM annotation.assertion") == 0
