"""Invariants of the annotation layer that the database holds, whatever path wrote the row."""

from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.annotate import AnnotationError, load
from envcite_ontology.verification.extract import shape_issues
from tests.reasoning_support import _plain_duty
from tests.test_annotation import LAW, _definition_doc, _doc, _duty, _limit
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import Conn, _record, _two_statements

pytestmark = pytest.mark.usefixtures("conn")


def _penalty(sid: str, punishes: str, kind: str = "sanction") -> dict[str, Any]:
    return {
        "id": sid,
        "law": LAW,
        "path": "art_3",
        "text": "丙一",
        "kind": kind,
        "sanction": {"punishes": punishes, "effect": "fine", "min": 1, "max": 2},
    }


def _three(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一", "丙一")))


def test_a_sanction_punishes_only_a_prescriptive_statement(conn: Conn, tmp_path: Path) -> None:
    _three(conn, tmp_path)
    with pytest.raises(AnnotationError, match="sanction_punishes_prescriptive"):
        load(conn, _doc(_limit("乙一", 100), _penalty("p", "limit100")))
    conn.rollback()


def test_only_a_sanction_statement_carries_a_sanction(conn: Conn, tmp_path: Path) -> None:
    _three(conn, tmp_path)
    with pytest.raises(AnnotationError, match="sanction_statement_is_sanction"):
        load(conn, _doc(_duty(), _penalty("p", "duty", kind="constitutive")))
    conn.rollback()


def test_a_punished_statement_cannot_change_its_kind(conn: Conn, tmp_path: Path) -> None:
    _three(conn, tmp_path)
    duty = load(conn, _doc(_duty(), _penalty("p", "duty"))).ids["duty"]
    with pytest.raises(psycopg.errors.IntegrityError):  # 已封存；未封存時由複合外鍵擋
        conn.execute(
            "UPDATE annotation.statement SET statement_kind = 'constitutive', norm_modality = NULL"
            " WHERE id = %s",
            (duty,),
        )
    conn.rollback()


def test_a_concept_value_is_only_compared_for_equality(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    duty = _duty()
    duty["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": "cod",
        "comparator": "le",
        "value_concept": "discharging",
    }
    with pytest.raises(AnnotationError, match="value_concept_is_equality"):
        load(conn, _doc(duty))
    conn.rollback()


def test_a_defined_term_belongs_to_a_scheme_of_its_own_law(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    doc = _definition_doc()
    doc["scheme"] = {"code": "shared", "title": "跨法共用"}
    with pytest.raises(AnnotationError, match="scoped to the law"):
        load(conn, doc)
    conn.rollback()


def test_the_extractor_cannot_verify_itself_even_by_a_direct_insert(
    conn: Conn, tmp_path: Path
) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    with pytest.raises(psycopg.errors.CheckViolation, match="extractor cannot verify"):
        conn.execute(
            "INSERT INTO annotation.verification (assertion_id, method, verifier, result)"
            " VALUES (%s, 'x', 'test', 'pass')",
            (duty,),
        )
    conn.rollback()


def _set_state(conn: Conn, sid: int, state: str) -> None:
    conn.execute("UPDATE annotation.assertion SET confirm_state = %s WHERE id = %s", (state, sid))


def _policy(conn: Conn, sid: int) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT verifier, evidence FROM annotation.verification"
        " WHERE assertion_id = %s AND method = 'policy' ORDER BY id",
        (sid,),
    ).fetchall()


def test_a_direct_state_change_is_recorded(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _set_state(conn, duty, "rejected")

    assert _policy(conn, duty) == [("direct", {"action": "rejected"})]


def test_a_manual_review_records_its_reason(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', '引文錯置')", (duty,))

    assert _policy(conn, duty) == [("review", {"action": "rejected", "reason": "引文錯置"})]
    with pytest.raises(psycopg.errors.CheckViolation, match="reason"):
        conn.execute("SELECT annotation.set_confirm_state(%s, 'confirmed', ' ')", (duty,))
    conn.rollback()


def test_a_statement_loaded_as_reviewed_is_recorded(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    duty = load(conn, _doc(_duty())).ids["duty"]  # review: confirmed

    assert _policy(conn, duty) == [("loaded", {"action": "confirmed"})]


def test_settle_records_once_and_does_not_lend_its_name(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    ids = load(conn, _doc(_plain_duty(), _limit("乙一", 100), review="extracted")).ids
    duty, other = ids["duty"], ids["limit100"]
    _record(conn, duty, "a", "pass")
    conn.execute("SELECT * FROM annotation.settle('statement', ARRAY['a'])")
    _set_state(conn, other, "rejected")

    assert [v for v, _ in _policy(conn, duty)] == ["settle"]
    assert [v for v, _ in _policy(conn, other)] == ["direct"]


def test_every_record_names_the_login_that_wrote_it(conn: Conn, tmp_path: Path) -> None:
    duty = _two_statements(conn, tmp_path).ids["duty"]
    _record(conn, duty, "a", "pass")

    assert _scalar(conn, "SELECT recorded_by = session_user FROM annotation.verification")


def test_the_extractor_is_asked_to_repair_what_the_database_would_refuse() -> None:
    answer = {
        "concepts": [{"code": "cod", "label": "化學需氧量"}],
        "statements": [
            {
                "id": "d",
                "kind": "prescriptive",
                "conditions": [
                    {"role": "duty", "concept": "cod", "comparator": "le", "value_concept": "x"}
                ],
            },
            {"id": "p1", "kind": "constitutive", "sanction": {"effect": "fine", "punishes": "d"}},
            {"id": "empty", "kind": "prescriptive", "sanction": {"effect": None}},
        ],
    }

    issues = shape_issues(answer, {"x": "某用語"})

    assert any("eq 或 ne" in p for p in issues["d"])
    assert any("kind 必須是 sanction" in p for p in issues["p1"])
    assert "empty" not in issues  # 照樣板填成空值的罰則欄位被忽略
