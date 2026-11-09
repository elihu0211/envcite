"""作答結果彙總：計數、口徑與輸入來源。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.answer_quality import (
    FINAL_ANSWERED,
    FINAL_MODEL_ERROR,
    FINAL_MODEL_INSUFFICIENT,
    FINAL_REFUSED_BELOW_FLOOR,
    FINAL_REFUSED_NO_EVIDENCE,
    FINAL_VOID_UNKNOWN_CITATION,
    InputError,
    evaluate_records,
    format_markdown_report,
)

# 動態載入 tools/natural_answer_eval.py，避免非套件目錄引發 mypy 模組路徑衝突
_tool_path = Path(__file__).resolve().parents[1] / "tools" / "natural_answer_eval.py"
_spec = importlib.util.spec_from_file_location("natural_answer_eval_module", _tool_path)
assert _spec is not None and _spec.loader is not None
_tool = importlib.util.module_from_spec(_spec)
sys.modules["natural_answer_eval_module"] = _tool
_spec.loader.exec_module(_tool)

GOLDEN_SHA = "a" * 64

META: dict[str, Any] = {
    "run_at": "2026-11-04T22:46:48+00:00",
    "envcite_commit": "13536a6474ea568a9edba6dea954b07fef210b4b",
    "git_dirty": True,
    "golden": {"path": "golden.yaml", "sha256": GOLDEN_SHA},
    "snapshots": [{"feed": "moj_law", "data_date": "2026-10-22"}],
    "model": "mistral-small-4",
    "model_calls": 135,
    "as_of": "2026-11-04",
}

RECORDS: list[dict[str, Any]] = [
    # 無答案題：下界攔截 1、放過 1
    {"id": "n-1", "bucket": "negative", "final": FINAL_REFUSED_BELOW_FLOOR},
    {"id": "n-2", "bucket": "negative", "final": FINAL_ANSWERED, "cited": ["L1"]},
    # 可答題：模型自認不足（證據含期望條）、引用查核作廢、含期望條、不含期望條、下界攔截、端點失敗
    {
        "id": "a-1",
        "bucket": "exact_id",
        "final": FINAL_MODEL_INSUFFICIENT,
        "evidence": 5,
        "evidence_has_expected": True,
    },
    {
        "id": "a-2",
        "bucket": "exact_id",
        "final": FINAL_VOID_UNKNOWN_CITATION,
        "evidence": 5,
        "evidence_has_expected": False,
    },
    {
        "id": "a-3",
        "bucket": "exact_id",
        "final": FINAL_ANSWERED,
        "evidence": 5,
        "evidence_has_expected": True,
        "cites_expected": True,
    },
    {
        "id": "a-4",
        "bucket": "paraphrase",
        "final": FINAL_ANSWERED,
        "evidence": 5,
        "evidence_has_expected": False,
        "cites_expected": False,
    },
    {"id": "a-5", "bucket": "paraphrase", "final": FINAL_REFUSED_NO_EVIDENCE},
    {
        "id": "a-6",
        "bucket": "paraphrase",
        "final": FINAL_MODEL_ERROR,
        "evidence": 5,
        "evidence_has_expected": True,
    },
]


def test_counts() -> None:
    s = evaluate_records(RECORDS)
    assert s.total_questions == 8

    assert (s.negative.total, s.negative.floor_refused, s.negative.answered) == (2, 1, 1)
    assert s.negative.not_answered == 1

    a = s.answerable
    assert a.total == 6
    assert (a.floor_refused, a.model_insufficient, a.verify_void) == (1, 1, 1)
    assert (a.answered, a.model_error) == (2, 1)
    # 端點失敗既不是回答、也不是拒答
    assert a.not_answered == 3

    c = s.citation
    assert (c.answered, c.cites_expected, c.cites_other) == (2, 1, 1)
    # 過下界的是沒被下界攔截的 5 題；其中證據含期望條的 a-1、a-3、a-6，最後沒回答的是 a-1、a-6
    assert (c.passed_floor, c.evidence_has_expected) == (5, 3)
    assert c.evidence_has_expected_not_answered == 2


def test_unknown_final_is_rejected() -> None:
    with pytest.raises(InputError, match="x-1"):
        evaluate_records([{"id": "x-1", "bucket": "exact_id", "final": "answered_maybe"}])


def test_answerable_answer_without_judgement_is_rejected() -> None:
    with pytest.raises(InputError, match="cites_expected"):
        evaluate_records([{"id": "x-2", "bucket": "exact_id", "final": FINAL_ANSWERED}])


def test_report_names_the_input_run_and_uses_no_hallucination_wording() -> None:
    meta = {**META, "input_name": "grounding-check-x.json", "input_sha256": "b" * 64}
    md = format_markdown_report(evaluate_records(RECORDS), meta)

    assert "2026-11-04T22:46:48+00:00" in md
    assert "13536a6474ea568a9edba6dea954b07fef210b4b" in md
    assert "工作樹有未提交變更" in md
    assert "b" * 64 in md
    assert "無答案題給出有引用回答的比例（上界）**：1／2（50.0%）" in md
    assert "有引用但未引到期望條的比例（無法判斷好壞）**：1／2（50.0%）" in md
    for banned in ("幻覺", "漂移", "p50", "p95", "Latency"):
        assert banned not in md


def _write_run(path: Path, records: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    path.write_text(json.dumps({"meta": meta, "results": records}), encoding="utf-8")


def test_cli_is_deterministic_and_records_input_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "run.json"
    _write_run(src, RECORDS, META)

    outs = []
    for name in ("one", "two"):
        monkeypatch.setattr(
            sys, "argv", ["natural_answer_eval", "--input", str(src), "--out", str(tmp_path / name)]
        )
        _tool.main()
        outs.append(
            ((tmp_path / f"{name}.md").read_text(), (tmp_path / f"{name}.json").read_text())
        )
    capsys.readouterr()

    assert outs[0] == outs[1]  # 報告不含本工具的執行時間
    doc = json.loads(outs[0][1])
    assert doc["input_run"]["envcite_commit"] == META["envcite_commit"]
    assert doc["input_run"]["input_name"] == "run.json"
    assert len(doc["input_run"]["input_sha256"]) == 64
    assert doc["summary"]["negative"]["answered"] == 1
    assert "latency" not in outs[0][1]


def test_subset_must_come_from_the_same_golden_set(tmp_path: Path) -> None:
    meta, results = _tool.load_run(_prepare_run(tmp_path))
    own = tmp_path / "split-own.json"
    other = tmp_path / "split-other.json"
    _tool.gs.write(
        own, {"golden_sha256": GOLDEN_SHA, "dev": ["n-1", "a-1"], "test": ["n-2", "a-3"]}
    )
    _tool.gs.write(other, {"golden_sha256": "c" * 64, "dev": ["n-1"], "test": ["n-2"]})

    picked = _tool.select_subset(meta, results, own, "test")
    assert [r["id"] for r in picked] == ["n-2", "a-3"]
    with pytest.raises(SystemExit, match="不是由這份題庫產生"):
        _tool.select_subset(meta, results, other, "test")


def test_subset_cannot_widen_a_run_of_another_subset(tmp_path: Path) -> None:
    src = tmp_path / "run.json"
    _write_run(src, RECORDS, {**META, "split": {"path": "s.json", "subset": "dev"}})
    meta, results = _tool.load_run(src)
    split = tmp_path / "split.json"
    _tool.gs.write(split, {"golden_sha256": GOLDEN_SHA, "dev": ["n-1"], "test": ["n-2"]})
    with pytest.raises(InputError, match="只跑了 dev"):
        _tool.select_subset(meta, results, split, "test")


def _prepare_run(tmp_path: Path) -> Path:
    src = tmp_path / "run.json"
    _write_run(src, RECORDS, META)
    return src
