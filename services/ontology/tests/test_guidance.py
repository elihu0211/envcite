"""Administrative rules (行政規則): loaded as immutable documents anchored on the text they
interpret, attached beside reasoning results by decision date, never premises of the reasoning."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite.ontology.v1 import reasoning_pb2, reasoning_pb2_grpc
from envcite_ontology.guidance import GuidanceError, load
from tests.conftest import call_metadata
from tests.test_annotation import LAW
from tests.test_grpc_reasoning import _case, _committed
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1
from tests.test_reasoning_transition import _amended

Conn = psycopg.Connection[tuple[object, ...]]
Stub = reasoning_pb2_grpc.ReasoningServiceStub


def _letter(no: str, issued_on: str, **over: Any) -> dict[str, Any]:
    return {
        "issuer": "環境部",
        "document_no": no,
        "issued_on": issued_on,
        "kind": "interpretation",
        "subject": f"{no}的主旨",
        "body": "說明：第一條所稱排放，包括間歇排放。",
        "interprets": [{"law": LAW, "path": "art_1", "text": "甲一"}],
        **over,
    }


def _doc(*letters: dict[str, Any], method: str = "human") -> dict[str, Any]:
    return {
        "derivation": {"method": method, "agent": "editor", "agent_version": "1"},
        "documents": list(letters),
    }


def _attached(conn: Conn, statement: int, decided: date) -> list[str]:
    return [
        str(no)
        for (no,) in conn.execute(
            "SELECT d.document_no FROM guidance.for_statements(%s, %s) AS g"
            "  JOIN guidance.document AS d ON d.id = g.document_id",
            ([statement], decided),
        )
    ]


def test_a_letter_is_anchored_on_the_text_it_interprets(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    assert (loaded.documents, loaded.interpretations, loaded.repeated) == (1, 1, 0)
    with pytest.raises(GuidanceError, match="現行任何版本"):
        load(
            conn,
            _doc(
                _letter(
                    "甲字第2號",
                    "2019-03-01",
                    interprets=[{"law": LAW, "path": "art_1", "text": "不是原文"}],
                )
            ),
        )


def test_loading_the_same_letter_again_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    again = load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    assert (again.repeated, again.interpretations) == (1, 0)
    assert _scalar(conn, "SELECT count(*) FROM guidance.document") == 1
    with pytest.raises(GuidanceError, match="內容與這次不同"):
        load(conn, _doc(_letter("甲字第1號", "2019-03-01", body="改過的說明")))


def test_a_model_does_not_load_administrative_rules(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(GuidanceError, match="not extracted by a model"):
        load(conn, _doc(_letter("甲字第1號", "2019-03-01"), method="llm"))


def test_a_later_letter_supersedes_from_its_issue_date(conn: Conn, tmp_path: Path) -> None:
    """釋字第 287 號：後函變更前釋示，前函自後函發文日起不再援用。"""
    duty, _ = _committed(conn, tmp_path)
    load(
        conn,
        _doc(
            _letter("甲字第1號", "2019-03-01"),
            _letter(
                "甲字第2號",
                "2019-05-01",
                supersedes=[{"issuer": "環境部", "document_no": "甲字第1號"}],
            ),
        ),
    )

    assert _attached(conn, duty, date(2019, 2, 1)) == []
    assert _attached(conn, duty, date(2019, 4, 1)) == ["甲字第1號"]
    assert _attached(conn, duty, date(2019, 5, 1)) == ["甲字第2號"]


def test_a_letter_can_cease_without_a_successor(conn: Conn, tmp_path: Path) -> None:
    duty, _ = _committed(conn, tmp_path)
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", ceased_on="2019-04-01")))

    assert _attached(conn, duty, date(2019, 3, 31)) == ["甲字第1號"]
    assert _attached(conn, duty, date(2019, 4, 1)) == []
    with pytest.raises(GuidanceError, match="cease before"):
        load(conn, _doc(_letter("甲字第3號", "2019-03-01", ceased_on="2019-01-01")))


def test_a_letter_beside_the_result_follows_the_decision_date(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    """行為早於發文、處分在發文之後：解釋性規定闡明法規原意，未確定的案件即可附上。"""
    duty, _ = _committed(conn, tmp_path)
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", published_in="行政院公報第25卷")))

    response = reasoning_stub.Evaluate(_case(), metadata=call_metadata())  # 行為 2019-01-01

    (g,) = response.at_decision.guidance
    assert (g.document_no, g.issued_on, g.kind) == (
        "甲字第1號",
        "2019-03-01",
        reasoning_pb2.GUIDANCE_KIND_INTERPRETATION,
    )
    assert list(g.statement_ids) == [duty] and g.published_in == "行政院公報第25卷"
    early = reasoning_stub.Evaluate(_case(decided_on="2019-02-01"), metadata=call_metadata())
    assert list(early.at_decision.guidance) == []


def test_an_amended_text_drops_the_interpretation_of_the_old_one(
    conn: Conn, tmp_path: Path
) -> None:
    """解釋錨定文字：第二條修正（乙一 → 乙二）後，舊文字的解釋不附在新文字的陳述旁。"""
    _, new_limit = _amended(conn, tmp_path)
    old_limit = int(
        _scalar(
            conn,
            "SELECT id FROM annotation.statement WHERE id <> %s AND statement_kind"
            " = 'constitutive'",
            new_limit,
        )
    )
    interprets = [{"law": LAW, "path": "art_2", "text": "乙一"}]
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", interprets=interprets)))

    assert _attached(conn, old_limit, date(2020, 6, 1)) == ["甲字第1號"]
    assert _attached(conn, new_limit, date(2020, 6, 1)) == []


def test_a_writer_adds_rules_but_cannot_rewrite_them(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    conn.execute("SET ROLE envcite_ontology_writer")
    try:
        load(conn, _doc(_letter("甲字第1號", "2019-03-01")))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE guidance.document SET body = '改寫'")
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")
