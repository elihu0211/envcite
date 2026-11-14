"""The independent-test gate: another model writes cases from the text alone, the engine judges
them."""

from pathlib import Path

from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.model_gates.independent import IndependentTestGate
from tests.model_gates_support import (
    CASES,
    EXTRACTOR,
    TEXT,
    Conn,
    Scripted,
    _extracted,
)
from tests.test_annotation import LAW
from tests.test_incremental import _articles, _first, _law_v1


def test_independent_cases_run_through_the_engine_and_must_agree(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    tester = Scripted("fake-b:tester", CASES)

    result = IndependentTestGate(tester).check(conn, sid)

    assert result is not None and result.verdict == "pass"
    assert result.evidence["agree"] == 3
    assert (
        TEXT in tester.prompts[0] and "規範性陳述" not in tester.prompts[0]
    )  # 只給原文與概念，不給標註


def test_the_tester_is_told_which_concepts_are_yes_or_no_and_which_are_numbers(
    conn: Conn, tmp_path: Path
) -> None:
    """不說型別，測試設計者會對成立與否的概念編出數值，與標註對不上。"""
    sid = _extracted(conn, tmp_path)
    tester = Scripted("fake-b:tester", CASES)

    IndependentTestGate(tester).check(conn, sid)

    assert "discharging：排放廢水（成立與否，成立為 1、不成立為 0）" in tester.prompts[0]
    assert "compliant：符合標準（成立與否，成立為 1、不成立為 0）" in tester.prompts[0]


def test_independent_cases_that_all_disagree_fail_and_a_mix_is_inconclusive(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    flipped = {
        "cases": [
            {"facts": {"discharging": 1, "compliant": 1}, "expected": "violation"},
            {"facts": {"discharging": 1, "compliant": 0}, "expected": "compliant"},
            {"facts": {"discharging": 0, "compliant": 1}, "expected": "violation"},
        ]
    }
    mixed = {"cases": [*CASES["cases"][:2], flipped["cases"][2]]}

    failed = IndependentTestGate(Scripted("fake-b:t", flipped)).check(conn, sid)
    undecided = IndependentTestGate(Scripted("fake-b:t", mixed)).check(conn, sid)
    assert failed is not None and failed.verdict == "fail"
    assert undecided is not None and undecided.verdict == "inconclusive"
    few = IndependentTestGate(Scripted("fake-b:t", {"cases": CASES["cases"][:2]})).check(conn, sid)
    assert few is not None and few.verdict == "inconclusive"


def test_independent_tests_do_not_apply_to_other_kinds_of_statement(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    answer = {"concepts": [], "statements": [{"id": "d", "quote": TEXT, "kind": "constitutive"}]}
    out = extract_unit(conn, Scripted(EXTRACTOR, answer), LAW, "art_1", agent_version="1")
    assert out.loaded is not None

    assert IndependentTestGate(Scripted("fake-b:t", CASES)).check(conn, out.loaded.ids["d"]) is None
