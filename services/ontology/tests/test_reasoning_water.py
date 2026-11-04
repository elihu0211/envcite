"""The reasoning engine on real text: Water Pollution Control Act arts. 7 and 40
(tests/sample.py), model-annotated and confirmed here."""

import json

import pytest

from envcite_ontology.annotate import Loaded
from envcite_ontology.reasoning import Result, evaluate
from tests.reasoning_support import (
    WATER,
    WATER_DAY,
    Conn,
)
from tests.test_import import _scalar
from tests.verification_support import _with_penalties


@pytest.fixture
def water(conn: Conn, sample: None) -> Loaded:
    loaded = _with_penalties(conn, json.loads(WATER.read_text(encoding="utf-8")))
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE assertion_kind = 'statement'"
    )
    conn.commit()
    return loaded


BUSINESS: dict[str, float] = {
    "actor_is_regulated_entity": 1,
    "discharges_to_surface_water": 1,
    "actor_is_business_or_sewer_system": 1,
    "actor_is_livestock_business": 0,
    "improvement_overdue": 0,
    "ap_intent": 1,  # 總則的有責性；這組案例測的是各法自己的罰則要件
}


def _effects(result: Result, water: Loaded, prefix: str) -> dict[str, tuple[str, bool]]:
    """Candidate penalties of one paragraph: statement label → (effect, conditional)."""
    labels = {v: k for k, v in water.ids.items() if k.startswith(prefix)}
    return {
        labels[p.statement_id]: (p.effect, p.conditional)
        for p in result.penalties
        if p.statement_id in labels
    }


def test_a_business_over_the_standard_gets_the_candidate_penalties_that_apply(
    conn: Conn, water: Loaded
) -> None:
    case = BUSINESS | {
        "effluent_meets_standard": 0,
        "circumstances_serious": 1,
        "revocation_necessary": 0,
    }

    result = evaluate(conn, WATER_DAY, case)

    assert result.verdicts == {water.ids["w7_1"]: "violation"}
    assert _effects(result, water, "p40_1") == {
        "p40_1_fine": ("fine", False),
        "p40_1_order": ("order", False),
        "p40_1_suspend": ("restriction", False),  # 情節重大：呼叫端判斷成立
    }  # 必要時不成立、屆期未逾：不列；畜牧業的罰則：對象不符，不列
    assert _effects(result, water, "p40_2") == {}
    fine = next(p for p in result.penalties if p.statement_id == water.ids["p40_1_fine"])
    assert (fine.min_amount, fine.max_amount) == (60000, 20000000)
    assert (water.ids["p40_1_suspend"], "circumstances_serious") in result.judgement_used


def test_complying_with_the_standard_leads_to_no_penalty(conn: Conn, water: Loaded) -> None:
    result = evaluate(conn, WATER_DAY, BUSINESS | {"effluent_meets_standard": 1})

    assert result.verdicts == {water.ids["w7_1"]: "compliant"}
    assert result.penalties == ()


def test_a_penalty_whose_own_requirement_needs_judgement_is_conditional(
    conn: Conn, water: Loaded
) -> None:
    """情節重大、必要時是不確定概念：沒有呼叫端的判斷，停工與廢止許可證是附條件，而非確定。"""
    result = evaluate(conn, WATER_DAY, BUSINESS | {"effluent_meets_standard": 0})

    effects = _effects(result, water, "p40_1")
    assert effects["p40_1_fine"] == ("fine", False)
    assert effects["p40_1_suspend"] == ("restriction", True)
    assert effects["p40_1_revoke"] == ("deprivation", True)
    assert set(result.needs_judgement) == {
        (water.ids["p40_1_suspend"], "circumstances_serious"),
        (water.ids["p40_1_revoke"], "revocation_necessary"),
    }
    assert result.missing == ()  # 缺的是判斷，不是事實


def test_an_overdue_improvement_adds_the_repeated_fine(conn: Conn, water: Loaded) -> None:
    case = BUSINESS | {
        "effluent_meets_standard": 0,
        "circumstances_serious": 0,
        "revocation_necessary": 0,
        "improvement_overdue": 1,
    }

    result = evaluate(conn, WATER_DAY, case)

    repeat = next(p for p in result.penalties if p.statement_id == water.ids["p40_1_repeat"])
    assert (repeat.effect, repeat.repeating, repeat.conditional) == ("fine", True, False)


def test_a_livestock_farm_matches_two_fines_and_the_priority_is_left_to_a_human(
    conn: Conn, water: Loaded
) -> None:
    """第四十條第二項（畜牧業）是否為第一項（事業）的特別規定，是解釋：未標註優先就列為未決。"""
    case = BUSINESS | {
        "actor_is_livestock_business": 1,
        "effluent_meets_standard": 0,
        "circumstances_serious": 0,
        "revocation_necessary": 0,
    }
    general, special = water.ids["p40_1_fine"], water.ids["p40_2_fine"]

    undecided = evaluate(conn, WATER_DAY, case | {"improvement_overdue": 1})
    assert (general, special) in undecided.unresolved_penalty_overlaps
    # 同一條的首次罰鍰與按次罰鍰不是替代關係，不算競合。
    assert (general, water.ids["p40_1_repeat"]) not in undecided.unresolved_penalty_overlaps

    conn.execute(
        "INSERT INTO annotation.statement_override (prevailing_id, subordinate_id, priority_basis)"
        " VALUES (%s, %s, 'lex_specialis')",
        (special, general),
    )
    conn.commit()
    decided = evaluate(conn, WATER_DAY, case)
    assert (general, special) not in decided.unresolved_penalty_overlaps
    assert (general, special, "lex_specialis") in decided.penalty_defeated


def test_the_cases_are_independent_of_database_row_order(conn: Conn, water: Loaded) -> None:
    case = BUSINESS | {"effluent_meets_standard": 0, "circumstances_serious": 1}

    first = evaluate(conn, WATER_DAY, case)
    second = evaluate(conn, WATER_DAY, case)

    assert first == second
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 13
