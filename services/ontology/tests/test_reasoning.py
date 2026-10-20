"""The reasoning engine on small hand-built annotations: dates, unknown and contradictory
facts, provisos, conflicts."""

from datetime import date
from pathlib import Path
from typing import Any

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import evaluate
from tests.reasoning_support import (
    Conn,
    _plain_duty,
    _scope,
    _with,
)
from tests.test_annotation import AFTER, BEFORE, LAW, _doc, _duty, _duty_condition, _limit
from tests.test_incremental import _first, _law_v1, _law_v2, _next


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
        "INSERT INTO annotation.statement_override (prevailing_id, subordinate_id, priority_basis)"
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


def test_an_ordering_comparison_on_a_concept_value_is_reported_not_silently_skipped(
    conn: Conn, tmp_path: Path
) -> None:
    """概念值只有屬於與否（eq、ne），沒有大小。"""
    _first(conn, tmp_path, _law_v1())
    by_concept = _plain_duty()
    by_concept["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": "cod",
        "comparator": "le",
        "value_concept": "discharging",
    }
    load(conn, _doc(by_concept))

    result = evaluate(conn, BEFORE, {"discharging": 1, "cod": 1})

    assert any(kind == "unsupported_condition" for kind, _ in result.problems)


def _belonging_rule() -> dict[str, Any]:
    """義務：排放者的行業須屬於「受管制事業」。"""
    return {
        "id": "duty",
        "law": LAW,
        "path": "art_1",
        "text": "甲一",
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            {
                "role": "scope",
                "clause": 1,
                "concept": "discharging",
                "comparator": "eq",
                "threshold": 1,
            },
            {
                "role": "duty",
                "clause": 1,
                "concept": "industry",
                "comparator": "eq",
                "value_concept": "regulated",
            },
        ],
    }


def _load_belonging(conn: Conn, *, definition_review: str = "confirmed") -> int:
    rule = _doc(_belonging_rule())
    rule["concepts"] = [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "industry", "label": "行業"},
        {"code": "regulated", "label": "受管制事業"},
    ]
    duty = load(conn, rule).ids["duty"]
    # 立法定義：限定在本法的方案，「受管制事業」涵蓋「工廠」。
    definition = _doc(review=definition_review)
    definition["scheme"] = {"code": f"def.{LAW}", "title": "本法用語", "law": LAW}
    definition["concepts"] = [
        {"code": "regulated", "label": "受管制事業"},
        {"code": "factory", "label": "工廠"},
    ]
    definition["definitions"] = [
        {
            "id": "d1",
            "law": LAW,
            "path": "art_1",
            "text": "甲一",
            "term": "regulated",
            "definition": "指工廠",
            "members": ["factory"],
        }
    ]
    load(conn, definition)
    return duty


def test_belonging_follows_the_statutory_definition(conn: Conn, tmp_path: Path) -> None:
    """「甲屬於乙」只依該法現行的用語定義：被定義涵蓋則屬於，否則不屬於。"""
    _first(conn, tmp_path, _law_v1())
    duty = _load_belonging(conn)

    covered = evaluate(conn, BEFORE, {"discharging": 1, "industry": "factory"})
    outside = evaluate(conn, BEFORE, {"discharging": 1, "industry": "school"})

    assert covered.verdicts == {duty: "compliant"}
    assert outside.verdicts == {duty: "violation"}
    assert covered.problems == ()


def test_belonging_without_a_confirmed_definition_is_not_assumed(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    duty = _load_belonging(conn, definition_review="extracted")

    result = evaluate(conn, BEFORE, {"discharging": 1, "industry": "factory"})

    assert result.verdicts == {duty: "violation"}  # 沒有已確認的定義涵蓋，不屬於


def test_a_missing_concept_value_is_listed_and_contradictory_ones_are_reported(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    duty = _load_belonging(conn)

    unknown = evaluate(conn, BEFORE, {"discharging": 1})
    both = evaluate(conn, BEFORE, {"discharging": 1, "industry": ["factory", "school"]})

    assert unknown.verdicts == {duty: "undetermined"}
    assert unknown.missing == ((duty, "industry"),)
    assert both.verdicts == {duty: "undetermined"}
    assert ("contradictory_fact", "industry") in both.problems
    assert both.missing == ()
