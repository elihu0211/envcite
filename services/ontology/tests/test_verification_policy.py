"""Verification records and the settle policy: when a statement becomes a fact (envcite-docs
02-06 §3.3)."""

from pathlib import Path

import psycopg
import pytest

from tests.verification_support import (
    Conn,
    _record,
    _review,
    _two_statements,
)


def test_the_extractor_cannot_verify_its_own_statement(conn: Conn, tmp_path: Path) -> None:
    loaded = _two_statements(conn, tmp_path)
    duty = loaded.ids["duty"]

    with pytest.raises(psycopg.errors.CheckViolation, match="extractor cannot verify"):
        conn.execute(
            "SELECT annotation.record_verification(%s, 'x', 'test', 'pass')", (duty,)
        )  # agent of _doc is "test"
    conn.rollback()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("SELECT annotation.record_verification(-1, 'x', 'tool', 'pass')")
    conn.rollback()


def test_a_statement_is_confirmed_only_when_every_required_method_passes(
    conn: Conn, tmp_path: Path
) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]

    _record(conn, duty, "a", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"  # b is missing

    _record(conn, duty, "b", "inconclusive")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"  # b is not conclusive

    _record(conn, duty, "b", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (1, 0)
    assert _review(conn, duty) == "confirmed"


def test_nothing_passed_means_nothing_confirmed(conn: Conn, tmp_path: Path) -> None:
    """全部 not_applicable 不是通過：沒有任何方法檢查過它，就不接受。"""
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "not_applicable")

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "extracted"
    _record(conn, duty, "b", "pass")
    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a','b'])").fetchone() == (1, 0)


def test_one_failure_rejects_even_when_the_required_methods_pass(
    conn: Conn, tmp_path: Path
) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    _record(conn, duty, "extra", "fail")  # 不在必要清單裡的方法失敗，同樣駁回

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 1)
    assert _review(conn, duty) == "rejected"


def test_the_latest_verdict_of_a_method_is_the_one_that_counts(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "fail")
    _record(conn, duty, "a", "pass")  # 修好之後重驗

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (1, 0)


def test_every_settlement_is_recorded_with_its_policy(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])")
    conn.commit()

    row = conn.execute(
        "SELECT verifier, verdict::text, evidence FROM annotation.verification"
        " WHERE statement_id = %s AND method = 'policy'",
        (duty,),
    ).fetchone()
    assert row == ("settle", "pass", {"action": "confirmed", "required": ["a"]})


def test_an_empty_policy_is_refused(conn: Conn) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("SELECT * FROM annotation.settle(ARRAY[]::text[])")
    conn.rollback()


def test_settled_statements_are_not_settled_again(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")
    conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])")
    _record(conn, duty, "a", "fail")  # 之後才失敗：不翻轉已確認的列（那是新一輪抽取與驗證的事）

    assert conn.execute("SELECT * FROM annotation.settle(ARRAY['a'])").fetchone() == (0, 0)
    assert _review(conn, duty) == "confirmed"
