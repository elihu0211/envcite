"""The ontology's conventions live in one place and every prompt that needs them is built from it.

Two real runs each found a convention told to one end only: the roundtrip reader was never told
that an authority clause carries no direction, and the judges were never told how a recurring
fine (按次處罰) is represented. A prompt that forgets the shared text is the defect."""

import pytest

from env_law_ontology.verification.conventions import CONVENTIONS, apply
from env_law_ontology.verification.extract import PROMPT
from env_law_ontology.verification.model_gates import JUDGE, REFORMALIZE


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param(PROMPT, id="extractor"),
        pytest.param(REFORMALIZE, id="roundtrip-reader"),
        pytest.param(JUDGE, id="panel-judge"),
    ],
)
def test_every_prompt_carries_the_shared_conventions(prompt: str) -> None:
    assert CONVENTIONS in prompt
    assert "{conventions}" not in prompt


def test_the_conventions_state_the_two_rules_the_runs_found_missing() -> None:
    assert "只有 kind 為 prescriptive 才有 deontic" in CONVENTIONS
    assert "repeating 為 true" in CONVENTIONS and "它自己的要件" in CONVENTIONS


def test_the_conventions_can_sit_inside_a_format_template() -> None:
    assert "{" not in CONVENTIONS and "}" not in CONVENTIONS
    assert apply("{conventions}\n{x}").format(x="ok").endswith("\nok")
