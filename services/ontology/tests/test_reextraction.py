"""A duty with no verdict is asked again with the reviewers' remarks; the database caps rounds."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from envcite_ontology.verification.reextract import due_units, review_notes
from envcite_ontology.verification.run import run
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED, Conn, FakeModel

TEXT = "排放廢水者應符合標準"
#: 沒有模型閘門，要求的方法永遠不會有結果：義務停在 extracted，沒有定論。
UNDECIDED = ["panel"]


def _go(conn: Conn, model: FakeModel, rounds: int) -> None:
    run(conn, model, [], [LAW], agent_version="1", required=UNDECIDED, reextract_rounds=rounds)


@pytest.fixture
def loaded(conn: Conn, tmp_path: Path) -> Conn:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    return conn


def test_a_unit_whose_duty_is_undecided_is_due_for_the_next_round(loaded: Conn) -> None:
    model = FakeModel(EXTRACTED)
    _go(loaded, model, 0)

    assert [(d.path, d.round) for d in due_units(loaded, "fake:model", "1", LAW)] == [("art_1", 1)]


def test_a_confirmed_duty_leaves_nothing_to_redo(loaded: Conn) -> None:
    model = FakeModel(EXTRACTED)
    run(
        loaded,
        model,
        [],
        [LAW],
        agent_version="1",
        required=["numbers", "references", "executable"],
    )

    assert due_units(loaded, "fake:model", "1", LAW) == []


def test_the_second_attempt_carries_the_review_and_the_first_does_not(loaded: Conn) -> None:
    model = FakeModel(EXTRACTED)
    _go(loaded, model, 1)

    reviewed = [p for p in model.prompts if "審查意見" in p]
    assert len(reviewed) == 1
    assert "引文「排放廢水者應符合標準」未被接受" in reviewed[0]
    assert "審查意見" not in model.prompts[0]


def test_review_notes_quote_what_the_reviewers_said(loaded: Conn) -> None:
    _go(loaded, FakeModel(EXTRACTED), 0)
    sid = int(str(_scalar(loaded, "SELECT id FROM annotation.statement LIMIT 1")))
    loaded.execute(
        "SELECT annotation.record_verification("
        "%s, 'panel', 'judge', 'fail'::annotation.gate_result, %s::jsonb)",
        (sid, Jsonb({"votes": [{"judge": "j", "faithful": False, "issues": ["遺漏期限"]}]})),
    )
    loaded.commit()
    row = loaded.execute(
        "SELECT provision_id, content_sha256 FROM annotation.statement WHERE id = %s", (sid,)
    ).fetchone()
    assert row is not None

    sha = row[1]
    assert isinstance(sha, bytes | memoryview)
    notes = review_notes(loaded, int(str(row[0])), bytes(sha))

    assert notes and "遺漏期限" in notes[0]


def test_a_run_cut_short_picks_up_at_the_round_each_unit_stopped(loaded: Conn) -> None:
    model = FakeModel(EXTRACTED)
    _go(loaded, model, 1)
    assert _scalar(loaded, "SELECT max(round) FROM annotation.extraction_attempt") == 1

    _go(loaded, model, 3)

    assert _scalar(loaded, "SELECT max(round) FROM annotation.extraction_attempt") == 3


def test_rounds_stop_at_three_and_the_database_refuses_a_fourth(loaded: Conn) -> None:
    model = FakeModel(EXTRACTED)
    _go(loaded, model, 3)
    _go(loaded, model, 3)

    assert _scalar(loaded, "SELECT max(round) FROM annotation.extraction_attempt") == 3
    assert due_units(loaded, "fake:model", "1", LAW) == []
    with pytest.raises(psycopg.errors.CheckViolation):
        loaded.execute("UPDATE annotation.extraction_attempt SET round = 4")
