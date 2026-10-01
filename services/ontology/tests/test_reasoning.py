"""Reasoning end to end (env-law-docs 02-05 §5): real schema, annotations loaded from documents,
case facts from the caller, conclusions out. No test double: statements, anchors and thresholds are
resolved by the SQL functions, then the general program runs on them."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from env_law_ontology.annotate import Loaded, load
from env_law_ontology.reasoning import Result, evaluate
from tests.test_annotation import AFTER, BEFORE, LAW, _doc, _duty, _duty_condition, _limit
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1, _law_v2, _next

Conn = psycopg.Connection[tuple[object, ...]]

WATER = Path(__file__).parent / "data" / "water_art7_art40.json"
WATER_DAY = date(2019, 1, 1)  # after the 2018-06-13 amendment, "並自公布日施行"


def _scope(concept: str, *, judgement: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {
        "role": "scope",
        "clause": 1,
        "concept": concept,
        "comparator": "eq",
        "threshold": 1,
    }
    if judgement:
        c["judgement"] = True
    return c


def _with(doc: dict[str, Any], *codes: str) -> dict[str, Any]:
    """The document plus concepts it mentions beyond the two the shared builder declares."""
    doc["concepts"] += [{"code": c, "label": c} for c in codes]
    return doc


def _plain_duty(text: str = "甲一", limit: int = 100) -> dict[str, Any]:
    """Art. 1: dischargers must keep COD at or under a literal limit."""
    return {
        "id": "duty",
        "law": LAW,
        "path": "art_1",
        "text": text,
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            _scope("discharging"),
            {
                "role": "duty",
                "clause": 1,
                "concept": "cod",
                "comparator": "le",
                "threshold": limit,
            },
        ],
    }


# ---- the same facts, two days: the limit comes from the authorising provision on the day ----


def test_the_same_facts_give_different_conclusions_on_different_days(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    assert _next(conn, tmp_path, _law_v2()).actions == {"versioned": 1}
    loaded = load(conn, _doc(_duty(), _limit("乙一", 100), _limit("乙二", 80)))
    case = {"discharging": 1, "cod": 90}

    before = evaluate(conn, BEFORE, case)
    after = evaluate(conn, AFTER, case)

    duty = loaded.ids["duty"]
    assert before.verdicts == {duty: "compliant"}
    assert after.verdicts == {duty: "violation"}
    # 依據軌跡：用了哪個事實值、哪個門檻值、門檻值取自哪個限值陳述。
    (entry,) = [t for t in after.trace if t.role == "duty"]
    assert (entry.fact, entry.threshold) == (90000, 80000)
    assert after.limit_sources[_duty_condition(conn)] == loaded.ids["limit80"]
    assert before.limit_sources[_duty_condition(conn)] == loaded.ids["limit100"]


def test_unknown_facts_are_listed_not_guessed(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_plain_duty()))

    result = evaluate(conn, BEFORE, {"discharging": 1})

    assert result.verdicts == {loaded.ids["duty"]: "undetermined"}
    assert result.missing == ((loaded.ids["duty"], "cod"),)
    assert result.problems == ()


def test_contradictory_facts_are_not_resolved_by_picking_one(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_plain_duty()))

    result = evaluate(conn, BEFORE, {"discharging": 1, "cod": [90, 150]})

    assert result.verdicts == {loaded.ids["duty"]: "undetermined"}
    assert ("contradictory_fact", "cod") in result.problems
    assert result.missing == ()  # 矛盾不冒充缺事實


def test_a_proviso_excludes_the_rule_or_makes_the_conclusion_conditional(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    proviso = {
        "id": "proviso",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "constitutive",
        "exception_of": "duty",
        "conditions": [_scope("force_majeure")],
    }
    loaded = load(conn, _with(_doc(_plain_duty(), proviso), "force_majeure"))
    duty = loaded.ids["duty"]
    over = {"discharging": 1, "cod": 150}

    assert evaluate(conn, BEFORE, over | {"force_majeure": 0}).verdicts == {duty: "violation"}
    assert evaluate(conn, BEFORE, over | {"force_majeure": 1}).verdicts == {duty: "excluded"}
    unknown = evaluate(conn, BEFORE, over)
    assert unknown.verdicts == {duty: "conditional_violation"}
    assert unknown.missing == ((duty, "force_majeure"),)


def test_a_proviso_that_needs_judgement_is_not_a_missing_fact(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    proviso = {
        "id": "proviso",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "constitutive",
        "exception_of": "duty",
        "conditions": [_scope("force_majeure", judgement=True)],
    }
    loaded = load(conn, _with(_doc(_plain_duty(), proviso), "force_majeure"))
    duty = loaded.ids["duty"]
    over = {"discharging": 1, "cod": 150}

    asked = evaluate(conn, BEFORE, over)
    assert asked.needs_judgement == ((duty, "force_majeure"),)
    assert asked.missing == ()

    answered = evaluate(conn, BEFORE, over | {"force_majeure": 1})
    assert answered.verdicts == {duty: "excluded"}
    assert (duty, "force_majeure") in answered.judgement_used  # 結論倚賴了呼叫端的判斷


def test_opposite_rules_on_the_same_action_conflict_unless_a_priority_is_annotated(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())

    def rule(sid: str, path: str, text: str, deontic: str) -> dict[str, Any]:
        return {
            "id": sid,
            "law": LAW,
            "path": path,
            "text": text,
            "kind": "prescriptive",
            "deontic": deontic,
            "slots": {"action": {"literal": "排放廢水"}},
            "conditions": [_scope("zone_a")],
        }

    doc = _with(
        _doc(
            rule("ban", "art_1", "甲一", "prohibition"),
            rule("permit", "art_2", "乙一", "permission"),
        ),
        "zone_a",
    )
    loaded = load(conn, doc)
    ban, permit = loaded.ids["ban"], loaded.ids["permit"]
    case = {"zone_a": 1}

    assert evaluate(conn, BEFORE, case).unresolved_conflicts == ((ban, permit),)

    conn.execute(
        "INSERT INTO annotation.statement_override (winner_id, loser_id, basis)"
        " VALUES (%s, %s, 'lex_specialis')",
        (permit, ban),
    )
    conn.commit()
    resolved = evaluate(conn, BEFORE, case)
    assert resolved.unresolved_conflicts == ()
    assert resolved.defeated == ((ban, permit, "lex_specialis"),)


def test_nothing_is_concluded_before_the_text_is_in_force(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_plain_duty()))

    assert evaluate(conn, date(2000, 1, 1), {"discharging": 1, "cod": 150}).verdicts == {}


def test_unreviewed_statements_conclude_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_plain_duty(), review="extracted"))

    assert evaluate(conn, BEFORE, {"discharging": 1, "cod": 150}).verdicts == {}


def test_a_condition_on_a_concept_value_is_reported_not_silently_skipped(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    by_concept = _plain_duty()
    by_concept["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": "cod",
        "comparator": "eq",
        "value_concept": "discharging",
    }
    load(conn, _doc(by_concept))

    result = evaluate(conn, BEFORE, {"discharging": 1, "cod": 1})

    assert any(kind == "unsupported_condition" for kind, _ in result.problems)


# ---- real text: 水污染防治法第 7、40 條 (tests/sample.py), model-annotated, confirmed here ----


@pytest.fixture
def water(conn: Conn, sample: None) -> Loaded:
    loaded = load(conn, json.loads(WATER.read_text(encoding="utf-8")))
    conn.execute("UPDATE annotation.statement SET review = 'confirmed'")
    conn.commit()
    return loaded


BUSINESS: dict[str, float] = {
    "actor_is_regulated_entity": 1,
    "discharges_to_surface_water": 1,
    "actor_is_business_or_sewer_system": 1,
    "actor_is_livestock_business": 0,
    "improvement_overdue": 0,
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
        "INSERT INTO annotation.statement_override (winner_id, loser_id, basis)"
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
