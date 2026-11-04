"""A draft annotation of real text (水污染防治法第 7、40 條，`tests/sample.py` 的節錄).

This is a fixture, not a corpus annotation: it covers two articles of the excerpt, was drafted
by a language model (`derivation.method = llm`), and no human has reviewed it. What it tests is
that real wording fits the schema and the loader, and that nothing unreviewed becomes a fact.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.annotate import Loaded, load
from tests.sample import WATER
from tests.test_import import _scalar
from tests.verification_support import _with_penalties

Conn = psycopg.Connection[tuple[object, ...]]

DATA = Path(__file__).parent / "data" / "water_art7_art40.json"
ON = date(2019, 1, 1)  # after the 2018-06-13 amendment, "並自公布日施行"


def _doc() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _resource(conn: Conn) -> int:
    return int(_scalar(conn, "SELECT id FROM corpus.legal_resource WHERE local_id = %s", WATER))


def _facts(conn: Conn) -> int:
    return int(
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.statements_at(%s, 'zho', %s)",
            _resource(conn),
            ON,
        )
    )


@pytest.fixture
def drafted(conn: Conn, sample: None) -> Loaded:
    return load(conn, _doc())


@pytest.fixture
def loaded(conn: Conn, sample: None) -> Loaded:
    """義務經審核確認、罰則連上之後。"""
    return _with_penalties(conn, _doc())


def test_the_draft_loads_against_the_real_wording(conn: Conn, drafted: Loaded) -> None:
    """罰則只處罰已確認的陳述：草稿裡的義務還沒確認，十個罰則存成待連結罰則。"""
    assert (drafted.statements, drafted.sanctions, drafted.held) == (3, 0, 10)
    assert (drafted.exceptions, drafted.overrides) == (0, 0)
    assert _scalar(conn, "SELECT count(*) FROM annotation.sanction") == 0
    states = conn.execute(
        "SELECT state, count(*) FROM annotation.held_penalty_state GROUP BY state"
    ).fetchall()
    assert states == [("awaiting_duty", 10)]  # 都等第 7 條第 1 項那一個義務


def test_the_penalties_are_written_once_the_duty_is_confirmed(conn: Conn, loaded: Loaded) -> None:
    assert (loaded.statements, loaded.sanctions) == (13, 10)
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement_condition") == 20
    assert conn.execute("SELECT DISTINCT state FROM annotation.held_penalty_state").fetchall() == [
        ("linked",)
    ]


def test_spans_select_the_sentence_each_statement_is_about(conn: Conn, loaded: Loaded) -> None:
    rows = conn.execute(
        "SELECT lower(s.span), upper(s.span), v.content FROM annotation.statement AS s"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE s.span IS NOT NULL"
    ).fetchall()
    quoted = {str(text)[int(lo) : int(hi)] for lo, hi, text in rows}  # type: ignore[call-overload]

    assert "處新臺幣六萬元以上二千萬元以下罰鍰" in quoted
    assert "情節重大者，得令其停工或停業" in quoted
    assert any(q.startswith("直轄市、縣（市）主管機關得視") for q in quoted)


def test_a_draft_nobody_reviewed_contributes_no_facts(conn: Conn, drafted: Loaded) -> None:
    assert _facts(conn) == 0


def test_once_confirmed_every_statement_is_a_fact_on_the_day(conn: Conn, loaded: Loaded) -> None:
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE assertion_kind = 'statement'"
    )
    conn.commit()

    assert _facts(conn) == 13


def test_the_penalties_punish_the_duty_and_carry_the_amounts(conn: Conn, loaded: Loaded) -> None:
    fines = conn.execute(
        "SELECT min_amount, max_amount FROM annotation.sanction"
        " WHERE legal_effect = 'fine' AND NOT per_occasion ORDER BY 1"
    ).fetchall()

    assert fines == [(6000, 600000), (60000, 20000000)]
    assert (
        _scalar(
            conn,
            "SELECT count(DISTINCT sn.punished_statement_id) FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS d ON d.id = sn.punished_statement_id"
            "   AND d.norm_modality = 'obligation'",
        )
        == 1
    )


def test_uncertain_legal_concepts_are_marked_as_needing_judgement(
    conn: Conn, loaded: Loaded
) -> None:
    marked = {
        str(code)
        for (code,) in conn.execute(
            "SELECT c.code FROM annotation.statement_condition AS sc"
            "  JOIN vocab.concept AS c ON c.id = sc.concept_id WHERE sc.needs_assessment"
        )
    }

    assert marked == {
        "circumstances_serious",
        "revocation_necessary",
        "jurisdiction_special_environment",
    }
    # 要件的其餘部分（適用範圍、義務內容）不是判斷。
    assert (
        _scalar(
            conn, "SELECT count(*) FROM annotation.statement_condition WHERE NOT needs_assessment"
        )
        == 15
    )


def test_the_improvement_order_and_the_repeated_fine_are_their_own_effects(
    conn: Conn, loaded: Loaded
) -> None:
    """一個罰則陳述一個效果：改善命令不是處罰種類，按次處罰是同一個罰鍰的反覆裁處。"""
    effects = conn.execute(
        "SELECT legal_effect::text, per_occasion, count(*) FROM annotation.sanction"
        " GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()

    assert effects == [
        ("deprivation", False, 2),
        ("fine", False, 2),
        ("fine", True, 2),
        ("order", False, 2),
        ("restriction", False, 2),
    ]
