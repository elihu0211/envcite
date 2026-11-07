"""Tests for ablation fusion study and threshold calibration tools."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ABLATION_TOOL = Path(__file__).resolve().parents[1] / "tools" / "ablation_fusion_eval.py"
CALIBRATION_TOOL = Path(__file__).resolve().parents[1] / "tools" / "threshold_calibration.py"


def _load_tool(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_rrf_fuse_logic() -> None:
    tool = _load_tool("ablation_fusion_eval", ABLATION_TOOL)
    lex = ["A", "B", "C"]
    sem = ["B", "D", "A"]

    # k=60, pool=3
    # A: 1/(60+1) + 1/(60+3) = 1/61 + 1/63 ≈ 0.01639 + 0.01587 = 0.03226
    # B: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 ≈ 0.01613 + 0.01639 = 0.03252 -> B 第一
    # C: 1/(60+3) = 1/63 ≈ 0.01587
    # D: 1/(60+2) = 1/62 ≈ 0.01613 -> D 在 C 前面
    fused = tool.rrf_fuse(lex, sem, k=60, pool=3)
    assert fused[0] == "B"
    assert fused[1] == "A"
    assert fused[2] == "D"
    assert fused[3] == "C"

    # 候選池截斷驗證：pool=1 只取各臂第 1 名
    fused_p1 = tool.rrf_fuse(lex, sem, k=60, pool=1)
    assert set(fused_p1) == {"A", "B"}


def test_run_ablation_grid() -> None:
    tool = _load_tool("ablation_fusion_eval", ABLATION_TOOL)
    qrels = {
        "q1": {"A": 2, "B": 1},
        "q2": {"C": 2},
    }
    lex_runs = {
        "q1": ["B", "A"],
        "q2": ["C", "D"],
    }
    sem_runs = {
        "q1": ["A", "B"],
        "q2": ["D", "C"],
    }

    report = tool.run_ablation(
        qrels,
        lex_runs,
        sem_runs,
        ["q1", "q2"],
        k_values=[20, 60],
        pool_values=[10, 50],
        metrics=["hit_rate@1", "mrr"],
        relevant_from=2,
    )

    assert report["num_questions"] == 2
    assert len(report["grid"]) == 4  # 2 k_values * 2 pool_values
    for cell in report["grid"]:
        assert "hit_rate@1" in cell["metrics"]
        assert "mrr" in cell["metrics"]


def test_threshold_truncation_gap_isolation() -> None:
    tool = _load_tool("threshold_calibration", CALIBRATION_TOOL)
    Scored = tool.ScoredQuestion

    # 構造數據：可答 2 題，負例 1 題
    # q1: top10 過下界，top50 過下界
    # q2: top10 沒過，top50 過下界（這就是 Top-10 截斷造成的落差！）
    # q_neg: top10 與 top50 都沒過下界
    qs = [
        Scored(
            qid="q1",
            bucket="exact_id",
            is_answerable=True,
            top10_lexical=1.0,
            top10_semantic=0.8,
            top50_lexical=1.0,
            top50_semantic=0.8,
        ),
        Scored(
            qid="q2",
            bucket="paraphrase",
            is_answerable=True,
            top10_lexical=0.2,
            top10_semantic=0.6,
            top50_lexical=0.6,
            top50_semantic=0.72,
        ),
        Scored(
            qid="q_neg",
            bucket="negative",
            is_answerable=False,
            top10_lexical=0.1,
            top10_semantic=0.5,
            top50_lexical=0.2,
            top50_semantic=0.55,
        ),
    ]

    analysis = tool.analyze_truncation_gap(qs, lexical_bound=0.5, semantic_bound=0.71)
    assert analysis["answerable_count"] == 2
    assert analysis["unanswerable_count"] == 1
    assert analysis["answerable_pass_top50"] == 2
    assert analysis["answerable_pass_top10"] == 1
    assert analysis["truncation_gap_answerable"] == 1  # 成功隔離出 1 題落差
    assert analysis["truncation_gap_unanswerable"] == 0

    # 門檻網格校準
    grid = tool.calibrate_thresholds(
        qs, lexical_grid=[0.5, 1.0], semantic_grid=[0.70, 0.75], limit_depth=10
    )
    assert len(grid) == 4
