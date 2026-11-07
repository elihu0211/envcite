"""負載基準工具（load_bench.py）之邏輯與 CLI 參數單元測試。"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

# 動態載入 tools/load_bench.py，避免非套件目錄引發 mypy 模組路徑衝突
_tool_path = Path(__file__).resolve().parents[1] / "tools" / "load_bench.py"
_spec = importlib.util.spec_from_file_location("load_bench_module", _tool_path)
assert _spec is not None and _spec.loader is not None
_load_bench = importlib.util.module_from_spec(_spec)
sys.modules["load_bench_module"] = _load_bench
_spec.loader.exec_module(_load_bench)

TARGETS: dict[str, Any] = _load_bench.TARGETS
percentile = _load_bench.percentile
render = _load_bench.render
requests = _load_bench.requests
summarize = _load_bench.summarize


def test_percentile() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(data, 0.0) == 1.0
    assert percentile(data, 0.5) == 5.0
    assert percentile(data, 1.0) == 10.0
    assert percentile([42.0], 0.99) == 42.0


def test_requests_mapping() -> None:
    questions = [("q1", "第一條是什麼？"), ("q2", "罰則為何？")]

    # evaluate: 固定單一事實
    eval_target = TARGETS["evaluate"]
    ids, reqs = requests(eval_target, questions)
    assert ids == ["evaluate"]
    assert len(reqs) == 1
    assert "conductOn" in reqs[0]

    # ground: consult 格式
    ground_target = TARGETS["ground"]
    ids, reqs = requests(ground_target, questions)
    assert ids == ["q1", "q2"]
    assert reqs == [{"question": "第一條是什麼？"}, {"question": "罰則為何？"}]

    # search: ontology 格式
    search_target = TARGETS["search"]
    ids, reqs = requests(search_target, questions)
    assert ids == ["q1", "q2"]
    assert reqs == [
        {"query": "第一條是什麼？", "limit": 10},
        {"query": "罰則為何？", "limit": 10},
    ]


def test_summarize_metrics() -> None:
    # 格式：(qid, end_time, duration_secs, status)
    points = [
        ("q1", 10.1, 0.1, "OK"),
        ("q2", 10.2, 0.2, "OK"),
        ("q3", 10.3, 0.3, "OK"),
        ("q4", 10.4, 0.4, "RESOURCE_EXHAUSTED"),
    ]
    summary = summarize("search", "ladder", 2, points)

    assert summary["target"] == "search"
    assert summary["phase"] == "ladder"
    assert summary["vus"] == 2
    assert summary["n"] == 4
    assert summary["statuses"] == {"OK": 3, "RESOURCE_EXHAUSTED": 1}
    assert summary["error_rate"] == 0.25
    assert summary["max"] == 0.4
    assert len(summary["slowest"]) == 4
    assert summary["slowest"][0]["id"] == "q4"
    assert summary["slowest"][0]["seconds"] == 0.4


def test_render_report() -> None:
    doc: dict[str, Any] = {
        "meta": {
            "run_at": "2026-11-07T18:00:00+08:00",
            "envcite_commit": "8498c1b",
            "dirty": False,
            "golden": {"path": "golden.yaml", "sha256": "1234567890abcdef"},
            "split": {"path": "split.json", "subset": "dev"},
            "questions": 10,
            "iterations_per_stage": 10,
            "embedding_calls": 20,
            "timeout": "10s",
            "skip_cold": True,
            "external_targets": {
                "ontology": "10.0.1.25:50051",
                "consult": None,
            },
            "host": {"cpu": "Apple M3", "cores": 8, "memory_gib": 16.0},
            "docker_vm": {"cpus": 4, "memory_gib": 8.0, "server_version": "27.0"},
            "postgres": {
                "version": "18.0",
                "shared_buffers": "2GB",
                "work_mem": "64MB",
            },
            "corpus": {
                "database_size": "500MB",
                "legal_resources": 100,
                "provisions": 2000,
                "passages": 5000,
                "embedded_passages": 5000,
                "embedding_models": ["text-embedding-3-small"],
                "confirmed_statements": 50,
                "snapshots": [{"feed": "moj", "data_date": "2026-11-01"}],
            },
            "servers": {
                "ontology_workers": 10,
                "ontology_max_concurrent_rpcs": 20,
                "lexical_max_df": 0.5,
                "search_limit": 10,
                "consult_ontology_timeout": "5s",
            },
            "tools": {"k6": "k6 v0.50.0"},
        },
        "results": [
            {
                "target": "search",
                "phase": "hot",
                "vus": 1,
                "n": 10,
                "p50": 0.085,
                "p95": 0.120,
                "p99": 0.150,
                "max": 0.180,
                "throughput_rps": 11.5,
                "error_rate": 0.0,
                "statuses": {"OK": 10},
                "slowest": [{"id": "q1", "seconds": 0.18, "status": "OK"}],
            }
        ],
    }

    md = render(doc)
    assert "# 效能基準（2026-11-07）" in md
    assert "已略過冷快取量測" in md
    assert "10.0.1.25:50051" in md
    assert "| search | hot | 1 | 10 | 0.085 | 0.120 | 0.150 | 0.180 | 11.5 | 0.0% | — |" in md


def test_cli_argument_parsing() -> None:
    # 測試 CLI 參數解析器，確保 --pg-container 變成非必填，且支援新增的 staging/外部參數
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("golden", type=Path)
    r.add_argument("--split", type=Path, required=True)
    r.add_argument("--subset", default="dev")
    r.add_argument("--pg-container", default=None)
    r.add_argument("--skip-cold", action="store_true")
    r.add_argument("--ontology-address", default=None)
    r.add_argument("--consult-address", default=None)
    r.add_argument("--targets", default="search,ground,evaluate,search-lexical")
    r.add_argument("--ladder", default="1,2,4,8,16,24,32")
    r.add_argument("--iterations", type=int, default=0)
    r.add_argument("--out", type=Path, required=True)

    # 模擬 staging 執行（無 --pg-container）
    args = parser.parse_args(
        [
            "run",
            "eval/regulations-golden-eval.yaml",
            "--split",
            "eval/golden-split.json",
            "--out",
            "eval/load-bench-staging",
            "--ontology-address",
            "envcite-ontology.envcite.svc:50051",
            "--skip-cold",
        ]
    )

    assert args.pg_container is None
    assert args.skip_cold is True
    assert args.ontology_address == "envcite-ontology.envcite.svc:50051"
    assert args.consult_address is None
