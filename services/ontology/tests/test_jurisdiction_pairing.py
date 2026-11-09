"""管轄與識別碼方案的配對由資料庫強制（0060）：非法配對在綱要層就被擋下，不靠寫入者守規矩。"""

from pathlib import Path

import psycopg
import pytest

from envcite_ontology.migrate import apply_all
from tests.test_migrate import _at, _drop

Conn = psycopg.Connection[tuple[object, ...]]

_INSERT = (
    "INSERT INTO corpus.legal_resource"
    " (jurisdiction, id_scheme, local_id, norm_kind, document_type)"
    " VALUES (%s, %s, %s, %s::corpus.norm_kind, '條例')"
)


def _jurisdictions(conn: Conn) -> list[str]:
    rows = conn.execute("SELECT unnest(enum_range(NULL::corpus.jurisdiction))::text").fetchall()
    return [str(j) for (j,) in rows]


def test_a_central_scheme_pairs_only_with_the_central_jurisdiction(conn: Conn) -> None:
    conn.execute(_INSERT, ("TW-CENTRAL", "moj_pcode", "A0000001", "statute"))

    for jurisdiction in _jurisdictions(conn):
        if jurisdiction == "TW-CENTRAL":
            continue
        with pytest.raises(psycopg.errors.CheckViolation, match="scheme_matches_jurisdiction"):
            conn.execute(_INSERT, (jurisdiction, "moj_pcode", "A0000002", "statute"))
        conn.rollback()


def test_a_local_scheme_pairs_only_with_a_local_jurisdiction(conn: Conn) -> None:
    with pytest.raises(psycopg.errors.CheckViolation, match="scheme_matches_jurisdiction"):
        conn.execute(_INSERT, ("TW-CENTRAL", "local_glrs", "TPE-1", "local_regulation"))
    conn.rollback()

    local = [j for j in _jurisdictions(conn) if j != "TW-CENTRAL"]
    assert len(local) == 22
    for jurisdiction in local:
        conn.execute(_INSERT, (jurisdiction, "local_glrs", "ENV-1", "local_regulation"))
    # 同一個在地識別碼在不同管轄是不同的法規；同一管轄內仍唯一。
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(_INSERT, ("TW-TPE", "local_glrs", "ENV-1", "local_regulation"))
    conn.rollback()


@pytest.mark.parametrize("jurisdiction", ["TW-XXX", "tw-tpe", "TW-", "", "CENTRAL"])
def test_a_jurisdiction_outside_the_closed_set_is_refused(conn: Conn, jurisdiction: str) -> None:
    """管轄是列舉，不是正則：形狀合法（TW-XXX）也不是縣市。"""
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        conn.execute(_INSERT, (jurisdiction, "moj_pcode", "A0000001", "statute"))
    conn.rollback()


def test_the_jurisdiction_has_no_default(conn: Conn) -> None:
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute(
            "INSERT INTO corpus.legal_resource (id_scheme, local_id, norm_kind, document_type)"
            " VALUES ('moj_pcode', 'A0000001', 'statute', '法律')"
        )
    conn.rollback()


def test_a_scheme_nobody_produces_is_not_in_the_schema(conn: Conn) -> None:
    schemes = [
        str(s) for (s,) in conn.execute("SELECT unnest(enum_range(NULL::corpus.id_scheme))::text")
    ]
    assert schemes == ["moj_pcode", "local_glrs"]
    columns = {
        str(c)
        for (c,) in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'corpus' AND table_name = 'legal_resource'"
        )
    }
    assert columns == {
        "id",
        "jurisdiction",
        "id_scheme",
        "local_id",
        "norm_kind",
        "document_type",
        "repealed_on",
    }


_LEGAL_ROW = (
    "INSERT INTO corpus.legal_resource (id, id_scheme, local_id, norm_kind, document_type)"
    " OVERRIDING SYSTEM VALUE VALUES (1, 'moj_pcode', 'T0000001', 'statute', '法律');"
    "INSERT INTO corpus.legal_expression"
    " (id, resource_id, language, version_date, title, text_period, text_status, text_authority)"
    " OVERRIDING SYSTEM VALUE"
    " VALUES (1, 1, 'zho', '2020-01-01', '測試法', '[2020-01-01,)', 'available', 'official_db');"
)


def test_the_upgrade_keeps_the_laws_and_the_name_view(dsn: str, tmp_path: Path) -> None:
    name = "pairing_upgrade"
    try:
        with _at(dsn, name, tmp_path, "0060") as conn:
            conn.execute(_LEGAL_ROW)
            conn.commit()
            assert apply_all(conn) == ["0060_jurisdiction_scheme_pairing.sql"]

            assert conn.execute(
                "SELECT jurisdiction::text, id_scheme::text, local_id FROM corpus.legal_resource"
            ).fetchall() == [("TW-CENTRAL", "moj_pcode", "T0000001")]
            assert conn.execute("SELECT pcode FROM guidance.law_name").fetchall() == [("T0000001",)]
    finally:
        _drop(dsn, name)


@pytest.mark.parametrize(
    "dirty",
    [
        "UPDATE corpus.legal_resource SET approval_authority = '環境部'",
        "UPDATE corpus.legal_resource SET invalidated_by = '釋字第748號'",
        "UPDATE corpus.legal_resource SET ratified_at = DATE '2020-01-01'",
        "UPDATE corpus.legal_resource SET id_scheme = 'ly_statute', local_id = 'x'",
        "UPDATE corpus.legal_resource SET jurisdiction = 'TW-XXX'",
    ],
)
def test_the_upgrade_refuses_rather_than_dropping_data(
    dsn: str, tmp_path: Path, dirty: str
) -> None:
    """遷移要移除的欄位或方案若有資料，整個遷移失敗，資料庫停在 0059。"""
    name = "pairing_refuses"
    try:
        with _at(dsn, name, tmp_path, "0060") as conn:
            conn.execute(_LEGAL_ROW)
            conn.execute(dirty)
            conn.commit()
            with pytest.raises(psycopg.errors.DataException):
                apply_all(conn)
            conn.rollback()
            assert conn.execute("SELECT max(filename) FROM schema_migration").fetchone() == (
                "0059_duty_context.sql",
            )
    finally:
        _drop(dsn, name)


def test_the_upgrade_refuses_an_unpaired_row(dsn: str, tmp_path: Path) -> None:
    name = "pairing_unpaired"
    try:
        with _at(dsn, name, tmp_path, "0060") as conn:
            conn.execute(_LEGAL_ROW)
            conn.execute("UPDATE corpus.legal_resource SET id_scheme = 'local_glrs'")
            conn.commit()
            with pytest.raises(psycopg.errors.CheckViolation):
                apply_all(conn)
    finally:
        _drop(dsn, name)
