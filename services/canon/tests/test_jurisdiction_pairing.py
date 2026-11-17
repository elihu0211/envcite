"""管轄與識別碼方案的配對由資料庫強制：非法配對在綱要層就被擋下，不靠寫入者守規矩。"""

import psycopg
import pytest

from canon.db import Conn

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
