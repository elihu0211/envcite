"""自然情境作答品質總結性評估之單元測試守門。"""

from __future__ import annotations

import math
from typing import Any

from envcite_ontology.answer_quality import (
    FINAL_ANSWERED,
    FINAL_MODEL_INSUFFICIENT,
    FINAL_REFUSED_BELOW_FLOOR,
    FINAL_VOID_NO_CITATION,
    FINAL_VOID_UNKNOWN_CITATION,
    calculate_percentiles,
    evaluate_records,
    format_markdown_report,
    simulate_realistic_latencies,
)


def test_calculate_percentiles_empty() -> None:
    res = calculate_percentiles([])
    assert res.count == 0
    assert res.mean_ms == 0.0
    assert res.p50_ms == 0.0


def test_calculate_percentiles_values() -> None:
    vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    res = calculate_percentiles(vals)
    assert res.count == 10
    assert math.isclose(res.mean_ms, 55.0)
    assert res.min_ms == 10.0
    assert res.max_ms == 100.0
    assert math.isclose(res.p50_ms, 55.0)
    assert math.isclose(res.p90_ms, 91.0)


def test_evaluate_records_refusal_and_hallucination() -> None:
    # 建立合成案例：
    # 2 題無答案：1 題下界拒答，1 題放過（幻覺！）
    # 4 題可答：1 題模型回證據不足，1 題查核作廢，2 題回答（1 題命中期望，1 題引用漂移）
    records: list[dict[str, Any]] = [
        # Negative
        {"bucket": "negative", "final": FINAL_REFUSED_BELOW_FLOOR},
        {"bucket": "negative", "final": FINAL_ANSWERED, "cited": ["L1"]},
        # Positive
        {
            "bucket": "exact_id",
            "final": FINAL_MODEL_INSUFFICIENT,
            "evidence_has_expected": True,
        },
        {"bucket": "exact_id", "final": FINAL_VOID_UNKNOWN_CITATION, "cited": ["L99"]},
        {
            "bucket": "exact_id",
            "final": FINAL_ANSWERED,
            "cited": ["L1"],
            "cites_expected": True,
            "evidence_has_expected": True,
        },
        {
            "bucket": "paraphrase",
            "final": FINAL_ANSWERED,
            "cited": ["L2"],
            "cites_expected": False,
            "evidence_has_expected": False,
        },
    ]

    summary = evaluate_records(records)
    assert summary.total_questions == 6

    # 拒答指標
    r = summary.refusal
    assert r.negative_total == 2
    assert r.negative_floor_refused == 1
    assert r.negative_answered_leaked == 1
    assert math.isclose(r.negative_refusal_rate, 0.5)

    assert r.positive_total == 4
    assert r.positive_model_insufficient == 1
    assert r.positive_verify_void == 1
    assert r.positive_answered == 2
    assert math.isclose(r.positive_refusal_rate, 0.5)

    # 幻覺指標
    h = summary.hallucination
    assert h.unfounded_count == 1
    assert math.isclose(h.unfounded_rate, 0.5)
    assert h.citation_drift_count == 1
    assert math.isclose(h.citation_drift_rate, 0.5)
    assert h.external_leak_void_count == 1
    assert h.total_answered == 3  # 1 neg + 2 pos
    assert h.hallucinated_answers == 2  # 1 unfounded + 1 drift
    assert math.isclose(h.composite_hallucination_rate, 2 / 3, abs_tol=1e-3)

    # 引用忠實度
    f = summary.faithfulness
    assert f.positive_answered == 2
    assert f.hits_expected_count == 1
    assert math.isclose(f.grounded_fidelity_rate, 0.5)
    assert math.isclose(f.end_to_end_coverage_rate, 0.25)
    assert f.evidence_has_expected_but_refused == 1


def test_simulate_realistic_latencies() -> None:
    records: list[dict[str, Any]] = [
        {"final": FINAL_REFUSED_BELOW_FLOOR},
        {"final": FINAL_ANSWERED},
        {"final": FINAL_VOID_NO_CITATION},
    ]
    latencies = simulate_realistic_latencies(records, seed=123)
    assert len(latencies["retrieval_grounding"]) == 3
    assert len(latencies["citation_verify"]) == 3
    assert len(latencies["total_end_to_end"]) == 3
    # 只有非下界攔截者有模型推理呼叫
    assert len(latencies["llm_inference"]) == 2


def test_format_markdown_report() -> None:
    records: list[dict[str, Any]] = [
        {"bucket": "negative", "final": FINAL_REFUSED_BELOW_FLOOR},
        {
            "bucket": "exact_id",
            "final": FINAL_ANSWERED,
            "cites_expected": True,
            "evidence_has_expected": True,
        },
    ]
    latencies = simulate_realistic_latencies(records, seed=123)
    summary = evaluate_records(records, latencies=latencies)
    md = format_markdown_report(summary, {"run_at": "2026-11-07T00:00:00Z"})

    assert "# 自然情境作答品質總結性評估報告" in md
    assert "拒答率與可答性守門" in md
    assert "幻覺率審計" in md
    assert "引用忠實度" in md
    assert "端到端延遲分佈" in md
    assert "retrieval_grounding" in md
