"""The ontology's conventions live in one place and every prompt that needs them is built from it.

Two real runs each found a convention told to one end only: the roundtrip reader was never told
that an authority clause carries no direction, and the judges were never told how a recurring
fine (按次處罰) is represented. A prompt that forgets the shared text is the defect."""

from collections.abc import Callable

import pytest

from envcite_ontology.verification import prompts

CONVENTIONS = prompts.conventions()

WITH_CONVENTIONS: dict[str, Callable[[], str]] = {
    "extractor": lambda: prompts.extract(
        law="L", path="art_1", text="t", context="", defined=None, punished_by=(), review=()
    ),
    "roundtrip-reader": lambda: prompts.reformalize(description="d"),
    "panel-judge": lambda: prompts.judge(context="", text="t", quote="q", description="d"),
}

WITHOUT_CONVENTIONS: dict[str, Callable[[], str]] = {
    "definition-extractor": lambda: prompts.definitions(
        law="L", path="art_2", text="t", context=""
    ),
    "definition-reader": lambda: prompts.reread(text="t", context=""),
    "penalty-linker": lambda: prompts.choose(text="t", candidates=[prompts.Candidate(1, "a", "q")]),
    "test-writer": lambda: prompts.write_tests(text="t", concepts=["a：甲"]),
}


@pytest.mark.parametrize("build", WITH_CONVENTIONS.values(), ids=WITH_CONVENTIONS.keys())
def test_every_prompt_carries_the_shared_conventions(build: Callable[[], str]) -> None:
    assert CONVENTIONS in build()


@pytest.mark.parametrize("build", WITHOUT_CONVENTIONS.values(), ids=WITHOUT_CONVENTIONS.keys())
def test_prompts_that_do_not_judge_an_annotation_leave_the_conventions_out(
    build: Callable[[], str],
) -> None:
    assert "本體的約定" not in build()


def test_the_conventions_state_the_two_rules_the_runs_found_missing() -> None:
    assert "只有 kind 為 prescriptive 才有 deontic" in CONVENTIONS
    assert "repeating 為 true" in CONVENTIONS and "它自己的要件" in CONVENTIONS


def test_the_conventions_forbid_borrowing_a_defined_term_as_the_duty() -> None:
    assert "不得借來當 duty 或 scope 的概念" in CONVENTIONS


def test_the_conventions_split_effects_by_clause_and_exclude_criminal_penalties() -> None:
    assert "引文只取自己效果的子句" in CONVENTIONS
    assert "罰金不是 fine" in CONVENTIONS
