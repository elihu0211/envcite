"""The liability basis on a penalty's subject slot: the actor's basis must be among them."""

from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import Result, evaluate
from tests.reasoning_support import Conn, _plain_duty, _scope, _with
from tests.test_annotation import BEFORE, LAW, _doc
from tests.test_incremental import _articles, _first, _law_v1

FACTS: dict[str, float] = {"discharging": 1, "cod": 150, "ap_intent": 1}


def _fine(subject: Any) -> dict[str, Any]:
    penalty: dict[str, Any] = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("discharging")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    if subject is not None:
        penalty["slots"] = {"subject": subject}
    return penalty


def _run(conn: Conn, tmp_path: Path, subject: Any, actor: list[str] | None) -> tuple[Result, int]:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    loaded = load(conn, _with(_doc(_plain_duty(), _fine(subject)), "serious"))
    return evaluate(conn, BEFORE, FACTS, actor_bases=actor), loaded.ids["fine"]


@pytest.mark.parametrize("actor", [None, ["act"], ["state"]])
def test_an_unannotated_subject_is_not_limited(conn: Conn, tmp_path: Path, actor: Any) -> None:
    result, fine = _run(conn, tmp_path, {"literal": "事業"}, actor)
    assert [p.statement_id for p in result.penalties] == [fine]
    assert not result.penalties[0].conditional
    assert result.penalties[0].subject_bases == ()
    assert result.subject_mismatch == ()


def test_the_penalty_carries_the_basis_of_its_subject(conn: Conn, tmp_path: Path) -> None:
    subject = [
        {"literal": "污染行為人", "liability": "act"},
        {"literal": "關係人", "liability": "state"},
    ]
    result, _ = _run(conn, tmp_path, subject, ["act"])
    assert result.penalties[0].subject_bases == ("act", "state")


def test_a_matching_basis_keeps_the_penalty(conn: Conn, tmp_path: Path) -> None:
    result, fine = _run(
        conn, tmp_path, {"literal": "關係人", "liability": "state"}, ["act", "state"]
    )
    assert [p.statement_id for p in result.penalties] == [fine]
    assert not result.penalties[0].conditional
    assert result.missing == ()


def test_a_different_basis_fails_the_subject(conn: Conn, tmp_path: Path) -> None:
    result, fine = _run(conn, tmp_path, {"literal": "關係人", "liability": "state"}, ["act"])
    assert result.penalties == ()
    assert result.subject_mismatch == (fine,)
    assert result.missing == ()


def test_no_stated_basis_is_a_missing_fact_and_a_conditional_penalty(
    conn: Conn, tmp_path: Path
) -> None:
    result, fine = _run(conn, tmp_path, {"literal": "關係人", "liability": "state"}, None)
    assert [(p.statement_id, p.conditional) for p in result.penalties] == [(fine, True)]
    assert (fine, "liability_basis") in result.missing
    assert result.subject_mismatch == ()


def test_an_alternative_without_a_basis_leaves_the_subject_open(conn: Conn, tmp_path: Path) -> None:
    subject = [{"literal": "關係人", "liability": "state"}, {"literal": "負責人"}]
    result, fine = _run(conn, tmp_path, subject, ["act"])
    assert [p.statement_id for p in result.penalties] == [fine]
    assert not result.penalties[0].conditional
    assert result.subject_mismatch == ()
