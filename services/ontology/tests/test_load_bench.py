"""負載基準工具（load_bench.py）之邏輯與 CLI 參數單元測試。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from google.protobuf import json_format, message_factory

# 動態載入 tools/load_bench.py，避免非套件目錄引發 mypy 模組路徑衝突
_tool_path = Path(__file__).resolve().parents[1] / "tools" / "load_bench.py"
_spec = importlib.util.spec_from_file_location("load_bench_module", _tool_path)
assert _spec is not None and _spec.loader is not None
_load_bench = importlib.util.module_from_spec(_spec)
sys.modules["load_bench_module"] = _load_bench
_spec.loader.exec_module(_load_bench)

TARGETS: dict[str, Any] = _load_bench.TARGETS
build_parser = _load_bench.build_parser
environment = _load_bench.environment
percentile = _load_bench.percentile
preflight = _load_bench.preflight
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


def _args(*extra: str) -> Any:
    return build_parser().parse_args(
        ["run", "golden.yaml", "--split", "split.json", "--out", "out", *extra]
    )


def test_parser_takes_the_options_the_readme_documents() -> None:
    args = _args(
        "--skip-cold",
        "--ontology-address",
        "envcite-ontology.envcite.svc:50051",
        "--consult-address",
        "envcite-consult.envcite.svc:50052",
    )
    assert args.pg_container is None and args.skip_cold is True
    assert args.ontology_address == "envcite-ontology.envcite.svc:50051"
    assert args.consult_address == "envcite-consult.envcite.svc:50052"
    assert args.subset == "dev" and args.targets == "search,ground,evaluate,search-lexical"


def test_preflight_refuses_what_cannot_be_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_load_bench.shutil, "which", lambda name: f"/usr/bin/{name}")

    targets, ladder = preflight(_args("--pg-container", "pg"))
    assert [t.name for t in targets] == ["search", "ground", "evaluate", "search-lexical"]
    assert ladder == [1, 2, 4, 8, 16, 24, 32]

    with pytest.raises(SystemExit, match="--skip-cold"):
        preflight(_args())  # 既不量冷也沒有可重啟的容器：不默默略過
    with pytest.raises(SystemExit, match="無法重啟"):
        preflight(_args("--pg-container", "pg", "--ontology-address", "host:50051"))
    with pytest.raises(SystemExit, match="不認得的對象"):
        preflight(_args("--skip-cold", "--targets", "search,nope"))

    monkeypatch.setattr(_load_bench.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="k6"):
        preflight(_args("--skip-cold"))


def test_targets_are_v2_methods_that_exist_and_accept_the_requests_sent() -> None:
    """方法名、proto 檔與請求本體都對真實的 v2 契約驗證（k6 在跑的時候才發現就太晚）。"""
    from envcite.ontology.v2 import ontology_pb2, reasoning_pb2

    files = {
        "OntologyService": ontology_pb2.DESCRIPTOR,
        "ReasoningService": reasoning_pb2.DESCRIPTOR,
    }
    questions = [("q1", "第一條是什麼？")]
    for name in ("search", "search-lexical", "evaluate"):
        target = TARGETS[name]
        full_service, rpc = target.method.split("/")
        assert full_service.startswith("envcite.ontology.v2.")
        service = files[full_service.rsplit(".", 1)[1]].services_by_name[
            full_service.rsplit(".", 1)[1]
        ]
        method = service.methods_by_name[rpc]
        assert (_load_bench.PROTO_ROOT / target.proto).is_file()

        _, reqs = requests(target, questions)
        message = message_factory.GetMessageClass(method.input_type)()
        json_format.ParseDict(reqs[0], message)  # 欄位名不對會拋 ParseError


def test_environment_records_unknowns_instead_of_inventing_values(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_load_bench, "probe", lambda *cmd: None)

    env = environment(dsn, None, local_ontology=False, local_consult=False)

    assert env["host"]["cpu"] is None and env["host"]["memory_gib"] is None
    assert env["docker_vm"] is None
    assert env["tools"]["k6"] is None and env["tools"]["dotnet"] is None
    # 量的是已部署的服務：本機的服務設定不是它的，不記
    assert env["servers"]["ontology_workers"] is None
    assert env["servers"]["lexical_max_df"] is None
    assert env["servers"]["consult_ontology_timeout"] is None
    assert env["servers"]["search_limit"] == 10  # 這個是本程式送出的請求自己帶的
    assert env["postgres"]["version"]  # 資料庫的值照實讀
    assert env["corpus"]["legal_resources"] == 0


def test_environment_reads_the_local_consult_timeout_from_its_settings(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVCITE_ONTOLOGY_DSN", dsn)
    env = environment(dsn, None, local_ontology=True, local_consult=True)

    assert env["servers"]["consult_ontology_timeout"]
    assert env["servers"]["ontology_workers"] == _load_bench.server.WORKERS


def test_render_says_unknown_for_what_was_not_measured(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_load_bench, "probe", lambda *cmd: None)
    env = environment(dsn, None, local_ontology=False, local_consult=False)
    doc: dict[str, Any] = {
        "meta": {
            "run_at": "2026-11-09T10:00:00+08:00",
            "envcite_commit": "abc",
            "dirty": False,
            "golden": {"path": "g.yaml", "sha256": "0" * 64},
            "split": {"path": "s.json", "subset": "dev"},
            "questions": 1,
            "iterations_per_stage": 1,
            "embedding_calls": 0,
            "timeout": "10s",
            "skip_cold": True,
            "external_targets": {"ontology": "h:1", "consult": None},
            **env,
        },
        "results": [],
    }
    md = render(doc)

    assert "主機 未知，" in md and "Docker VM 未知 核" in md
    assert "ontology 執行緒 未知" in md and "consult 對 ontology 期限 未知" in md
    assert "5s" not in md
