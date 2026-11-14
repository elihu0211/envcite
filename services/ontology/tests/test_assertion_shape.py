"""A claim's content is sealed once written: the same content is one claim, sealed content does
not change, slots list alternatives, and a defined term stays in its law's scheme."""

from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.annotate import load
from envcite_ontology.verification.checks.shape import tidy
from tests.test_annotation import LAW, _definition_doc, _doc, _duty, _limit
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1


def _claims(conn: psycopg.Connection[tuple[object, ...]]) -> int:
    return int(_scalar(conn, "SELECT count(*) FROM annotation.assertion"))


def _with_slots(slots: dict[str, Any], span: list[int] | None = None) -> dict[str, Any]:
    duty = {**_duty(), "slots": slots}
    if span is not None:
        duty["span"] = span
    return duty


Conn = psycopg.Connection[tuple[object, ...]]


def test_loading_the_same_document_twice_adds_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    statements, definitions = _doc(_duty(), _limit("乙一", 100)), _definition_doc()
    first = load(conn, statements)
    load(conn, definitions)
    before = _claims(conn)

    again = load(conn, statements)
    defined = load(conn, definitions)

    assert again.ids == first.ids and again.duplicates == 2 and again.statements == 2
    assert defined.duplicates == 1 and defined.definitions == 1
    assert _claims(conn) == before
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement_condition") == 3


def test_a_rejected_claim_does_not_block_loading_it_again(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    old = load(conn, _doc(_duty(), review="extracted")).ids["duty"]
    conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', '重新標註')", (old,))

    new = load(conn, _doc(_duty(), review="extracted")).ids["duty"]

    assert new != old
    with pytest.raises(psycopg.errors.UniqueViolation):  # 駁回的不能復活成第二份相同的主張
        conn.execute("SELECT annotation.set_confirm_state(%s, 'extracted', '誤駁')", (old,))
    conn.rollback()


def test_span_and_the_order_of_alternatives_are_not_content(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    a = [{"literal": "事業"}, {"literal": "負責人"}]
    first = load(conn, _doc(_with_slots({"subject": a}, span=[0, 2]), review="extracted"))

    second = load(conn, _doc(_with_slots({"subject": a[::-1]}, span=[0, 1]), review="extracted"))

    assert second.ids == first.ids and second.duplicates == 1


def test_alternatives_are_kept_as_several_values_of_one_role(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    slots = {"subject": [{"literal": "事業"}, {"literal": "負責人"}], "action": {"literal": "排放"}}
    load(conn, _doc(_with_slots(slots)))

    rows = conn.execute(
        "SELECT slot_role::text, alternative, literal FROM annotation.statement_slot"
        " ORDER BY slot_role, alternative"
    ).fetchall()
    assert rows == [("action", 1, "排放"), ("subject", 1, "事業"), ("subject", 2, "負責人")]


def test_penalties_for_different_duties_are_different_claims(conn: Conn, tmp_path: Path) -> None:
    """「違反第一條或第二條者，處…」：效果相同、處罰的陳述不同，是兩個罰則。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一", "丙一")))
    other = {**_duty("乙一"), "id": "duty2", "path": "art_2"}

    def penalty(sid: str, punishes: str) -> dict[str, Any]:
        return {
            "id": sid,
            "law": LAW,
            "path": "art_3",
            "text": "丙一",
            "kind": "sanction",
            "sanction": {"punishes": punishes, "effect": "fine", "min": 1, "max": 2},
        }

    loaded = load(conn, _doc(_duty(), other, penalty("p1", "duty"), penalty("p2", "duty2")))

    assert loaded.duplicates == 0 and loaded.ids["p1"] != loaded.ids["p2"]


def test_sealed_content_cannot_change(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    duty = load(conn, _doc(_duty())).ids["duty"]

    with pytest.raises(psycopg.errors.CheckViolation, match="sealed"):
        conn.execute(
            "INSERT INTO annotation.statement_slot (statement_id, slot_role, literal)"
            " VALUES (%s, 'object', '廢水')",
            (duty,),
        )
    conn.rollback()


def test_a_claim_must_be_sealed_before_commit(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty()))
    provision, digest = conn.execute(
        "SELECT provision_id, content_sha256 FROM annotation.statement"
    ).fetchone()  # type: ignore[misc]
    claim = _scalar(
        conn,
        "INSERT INTO annotation.assertion (assertion_kind, derivation_id)"
        " SELECT 'statement', id FROM annotation.derivation RETURNING id",
    )
    conn.execute(
        "INSERT INTO annotation.statement (id, provision_id, content_sha256, statement_kind)"
        " VALUES (%s, %s, %s, 'constitutive')",
        (claim, provision, digest),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="not sealed"):
        conn.commit()


def test_a_defined_term_stays_in_the_scheme_of_its_law(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _definition_doc())
    conn.execute("INSERT INTO vocab.concept_scheme (code, title) VALUES ('shared', '共用')")

    with pytest.raises(psycopg.errors.CheckViolation, match="scoped to its law"):
        conn.execute(
            "UPDATE vocab.concept SET scheme_id = (SELECT id FROM vocab.concept_scheme"
            " WHERE code = 'shared') WHERE code = 'business'"
        )
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation, match="scoped to its law"):
        conn.execute(
            "UPDATE vocab.concept_scheme SET scope_resource_id = NULL WHERE scope_resource_id"
            " IS NOT NULL"
        )
    conn.rollback()


def test_the_extractor_may_list_alternatives_as_plain_strings() -> None:
    answer = {"statements": [{"id": "s", "slots": {"subject": ["事業", {"concept": "x"}]}}]}

    assert tidy(answer)["statements"][0]["slots"] == {
        "subject": [{"literal": "事業"}, {"concept": "x"}]
    }
