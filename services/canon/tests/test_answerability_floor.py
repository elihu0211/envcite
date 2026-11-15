"""The floor tool: join the two reports by question and count passes the way consult does."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "answerability_floor.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("answerability_floor", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["answerability_floor"] = module
    spec.loader.exec_module(module)
    return module


def _report(
    arm: str,
    rows: list[tuple[str, str, str, float | None, float | None]],
    subset: str = "dev",
) -> dict[str, Any]:
    return {
        "meta": {"split": {"sha256": "s", "subset": subset}},
        "arms": {
            arm: {
                "results": [
                    {
                        "id": i,
                        "bucket": bucket,
                        "status": status,
                        "top_lexical": lexical,
                        "top_semantic": semantic,
                    }
                    for i, bucket, status, lexical, semantic in rows
                ]
            }
        },
    }


def test_the_reports_are_joined_by_question_and_unmapped_ones_are_left_out() -> None:
    tool = _tool()
    lexical = _report(
        "lexical",
        [
            ("n", "negative", "negative", 2.0, None),
            ("a", "exact_id", "scored", 3.0, None),
            ("u", "exact_id", "unmapped", 9.0, None),
        ],
    )
    semantic = _report(
        "hybrid",
        [
            ("n", "negative", "negative", 1.0, 0.6),
            ("a", "exact_id", "scored", 1.0, 0.7),
            ("u", "exact_id", "unmapped", 1.0, 0.9),
        ],
    )

    rows = tool.join(lexical, semantic)

    assert [(r.negative, r.lexical, r.semantic) for r in rows] == [
        (True, 2.0, 0.6),
        (False, 3.0, 0.7),
    ]


def test_a_question_passes_when_either_arm_reaches_its_floor() -> None:
    tool = _tool()
    rows = [
        tool.Row(True, 0.0, 0.7),  # negative, caught by the semantic arm
        tool.Row(True, 0.0, 0.1),
        tool.Row(False, 5.0, 0.1),  # answerable, caught by the lexical arm
        tool.Row(False, 0.1, 0.1),
    ]

    p = tool.evaluate(rows, 4.0, 0.65)

    assert (p.negatives_pass, p.answerable_pass) == (1, 1)
    assert p.j == pytest.approx(0.0)
    assert tool.evaluate(rows, float("inf"), float("inf")).j == pytest.approx(0.0)


def test_a_question_missing_from_the_lexical_run_is_an_error() -> None:
    tool = _tool()
    with pytest.raises(ValueError, match="missing"):
        tool.join(
            _report("lexical", []), _report("hybrid", [("a", "exact_id", "scored", 1.0, 0.7)])
        )


def test_floors_are_chosen_on_the_dev_subset_only() -> None:
    tool = _tool()
    row: list[tuple[str, str, str, float | None, float | None]] = [
        ("n1", "negative", "negative", 1.0, 0.5)
    ]
    with pytest.raises(SystemExit, match="開發集"):
        tool.join(_report("lexical", row, "test"), _report("hybrid", row, "test"))
    with pytest.raises(SystemExit, match="開發集"):
        tool.join(_report("lexical", row), _report("hybrid", row, "test"))
