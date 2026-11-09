"""RRF ablation and the answerability-floor truncation tool.

The offline fusion has to be the service's own rule: it is checked against a real
`retrieval.search_hybrid`. The truncation tool must refuse a report that lacks the per-depth scores
instead of reading the missing depth as "the same", which showed a gap of zero for every input.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

from envcite_ontology import projection
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter
from tests.sample import _law, stored_snapshot
from tests.test_hybrid import FakeEmbedder

Conn = psycopg.Connection[tuple[object, ...]]

TOOLS = Path(__file__).resolve().parents[1] / "tools"
ON = "2019-06-01"
HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
THEMES = ("廢水", "噪音", "地震", "建物", "空氣", "排放", "檢查", "罰鍰")
LAWS = {"T0000001": "甲污染法", "T0000002": "乙管制法"}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import(conn: Conn, tmp_path: Path) -> None:
    laws = [
        _law(
            pcode,
            name,
            HISTORY,
            "20180613",
            [
                (str(n), f"{THEMES[(n + i) % 8]}與{THEMES[(n * 3 + i) % 8]}之管理，應依本法辦理。")
                for n in range(1, 9)
            ],
        )
        for i, (pcode, name) in enumerate(LAWS.items())
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())


# --- 融合規則 ---------------------------------------------------------------


def test_rrf_sums_the_reciprocal_ranks_of_the_first_pool_hits_of_each_arm() -> None:
    tool = _load("ablation_fusion_eval")
    lex = [(1, "A"), (2, "B"), (3, "C")]
    sem = [(2, "B"), (4, "D"), (1, "A")]

    # k = 60、pool = 3：A = 1/61 + 1/63、B = 1/62 + 1/61（最高）、D = 1/62、C = 1/63
    assert tool.rrf_fuse(lex, sem, k=60, pool=3) == ["B", "A", "D", "C"]
    # pool = 1 只取各臂第一名
    assert set(tool.rrf_fuse(lex, sem, k=60, pool=1)) == {"A", "B"}


def test_equal_scores_are_ordered_by_version_id_not_by_the_name_of_the_article() -> None:
    """兩臂的第一名各自只出現在一臂，分數相同；次序是版本編號，與條文代號的字典序無關。"""
    tool = _load("ablation_fusion_eval")
    assert tool.rrf_fuse([(9, "A")], [(3, "Z")], k=60, pool=1) == ["Z", "A"]
    assert tool.rrf_fuse([(3, "A")], [(9, "Z")], k=60, pool=1) == ["A", "Z"]


def test_k_decides_whether_an_article_in_both_arms_beats_a_first_place_in_one() -> None:
    tool = _load("ablation_fusion_eval")
    lex = [(1, "A"), (5, "X"), (2, "B")]
    sem = [(3, "C"), (6, "Y"), (2, "B")]
    # k = 0：第一名得 1，兩臂的第三名相加只有 2/3；k = 60：差距被壓平，兩臂都有的 B 勝出
    assert tool.rrf_fuse(lex, sem, k=0, pool=3)[0] in {"A", "C"}
    assert tool.rrf_fuse(lex, sem, k=60, pool=3)[0] == "B"


def test_the_service_list_is_classified_against_the_fused_list() -> None:
    tool = _load("ablation_fusion_eval")
    fused = ["A", "B", "C", "D"]
    # 罰則條 S1 插在 A 之後；條數上限截掉尾端
    assert tool.service_relation(["A", "S1", "B", "C", "D"], fused) == "identical"
    assert tool.service_relation(["A", "S1", "B"], fused) == "identical"
    # 罰則帶出把融合清單後段的 D 拉到 A 之後；上限截掉其間的 B
    assert tool.service_relation(["A", "D", "B", "C"], fused) == "reordered"
    assert tool.service_relation(["A", "D"], fused) == "reordered"
    # 服務的清單沒有任何融合清單的條
    assert tool.service_relation(["S1"], fused) == "different"
    assert tool.service_relation([], []) == "identical"


def test_holm_scales_by_the_remaining_tests_and_never_decreases() -> None:
    tool = _load("ablation_fusion_eval")
    got = tool.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert got == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    assert tool.holm({"x": 0.9}) == {"x": 0.9}


# --- 與服務一致 -------------------------------------------------------------


def test_offline_fusion_returns_the_very_list_the_service_returns(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    tool = _load("ablation_fusion_eval")
    _import(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    conn.commit()
    queries = [f"{a}{b}" for a in THEMES for b in THEMES if a != b][:30]
    with OntologyStore.from_dsn(dsn) as store, psycopg.connect(dsn) as read:
        segmenter = Segmenter(store.lexicon())
        for query in queries:
            got = tool.collect(
                read,
                terms=segmenter.to_tsvector_input(query),
                tokenizer=segmenter.tokenizer_id,
                on=ON,
                vector=embedder.embed([query])[0],
                model=embedder.model,
                pools=(3, 8),
                laws=(),
            )
            for pool, lists in got.items():
                assert lists["lexical"], query
                assert lists["semantic"], query
                fused = tool.rrf_fuse(lists["lexical"], lists["semantic"], k=60, pool=pool)
                assert fused[:pool] == [d for _, d in lists["hybrid"]], (query, pool)


def test_the_semantic_arm_runs_without_the_lexical_arm_and_the_lexical_arm_without_the_semantic(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    """「放流水」與「廢水」同一個概念軸，但沒有條文含這個詞：字面臂空，語意臂仍有候選。"""
    tool = _load("ablation_fusion_eval")
    _import(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    conn.commit()
    with OntologyStore.from_dsn(dsn) as store, psycopg.connect(dsn) as read:
        segmenter = Segmenter(store.lexicon())
        common = {
            "tokenizer": segmenter.tokenizer_id,
            "on": ON,
            "model": embedder.model,
            "pool": 5,
            "resources": None,
        }
        vector = tool.to_sql(embedder.embed(["放流水"])[0])
        terms = segmenter.to_tsvector_input("放流水")
        both_off = tool.arm_rows(read, terms="", embedding_sql=None, **common)
        semantic = tool.arm_rows(read, terms="", embedding_sql=vector, **common)
        lexical = tool.arm_rows(read, terms=terms, embedding_sql=None, **common)
        text = tool.arm_rows(
            read,
            terms=segmenter.to_tsvector_input("廢水"),
            embedding_sql=None,
            **common,
        )
        probe = tool.near_probe(read, vector, embedder.model, 7)

    assert 0 < probe["returned_rows"] <= 7 and probe["limit"] == 7
    assert probe["plan_nodes"]
    assert both_off == [] and lexical == []
    assert semantic
    assert text
    assert len(semantic) <= 5


# --- 格子 -------------------------------------------------------------------


def _arms(path: Path, *, hybrid_swapped: bool = False) -> Path:
    gs = _load("golden_set")
    lex = [[1, "A-art-1"], [2, "A-art-2"], [3, "A-art-3"]]
    sem = [[2, "A-art-2"], [4, "A-art-4"], [1, "A-art-1"]]
    fused = [[2, "A-art-2"], [1, "A-art-1"], [4, "A-art-4"], [3, "A-art-3"]]
    if hybrid_swapped:
        fused = fused[::-1]
    gs.write(
        path,
        {
            "kind": "fusion-arms",
            "run_at": "2026-11-09T00:00:00+00:00",
            "golden_sha256": "g",
            "split_sha256": "s",
            "subset": "dev",
            "snapshots": [],
            "envcite_commit": "c",
            "retrieval": {"pools": [50]},
            "service_path_check": None,
            "pools": {
                "50": {"lexical": {"q1": lex}, "semantic": {"q1": sem}, "hybrid": {"q1": fused}}
            },
        },
    )
    return path


def _qrels(path: Path) -> Path:
    gs = _load("golden_set")
    gs.write(
        path,
        {
            "relevant_from": 2,
            "qrels": {"q1": {"A-art-2": 2, "A-art-1": 0}},
        },
    )
    return path


def _grid_args(tmp_path: Path, arms: list[Path]) -> argparse.Namespace:
    return argparse.Namespace(
        qrels=_qrels(tmp_path / "qrels.json"),
        arms=arms,
        ks=[60],
        other_arms=None,
        golden=None,
        only=None,
        export_runs=tmp_path / "runs",
        out=tmp_path / "out.json",
    )


def test_a_grid_stops_when_the_offline_fusion_differs_from_the_service(tmp_path: Path) -> None:
    tool = _load("ablation_fusion_eval")
    with pytest.raises(SystemExit, match="不一致"):
        tool.cmd_grid(_grid_args(tmp_path, [_arms(tmp_path / "a.json", hybrid_swapped=True)]))


def test_fewer_than_three_rounds_never_claim_a_difference(tmp_path: Path) -> None:
    tool = _load("ablation_fusion_eval")
    tool.cmd_grid(_grid_args(tmp_path, [_arms(tmp_path / "a.json")]))
    report: dict[str, Any] = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["baseline"] == "P50-k60"
    assert report["rounds"] == 1
    assert report["fidelity_to_service"][0]["50"] == {"questions": 1, "identical": 1}
    assert report["cells"]["P50-k60"]["mean"]["hit_rate@1"] == 1.0
    assert (tmp_path / "runs" / "P50-k60.r1.run.json").exists()


def _systems() -> tuple[dict[str, dict[str, int]], dict[str, list[str]], dict[str, list[str]]]:
    qrels = {f"q{i}": {f"d{i}": 2} for i in range(20)}
    better = {q: [f"d{q[1:]}", "x"] for q in qrels}
    worse = {q: ["x1", "x2", "x3", f"d{q[1:]}"] for q in qrels}
    return qrels, better, worse


def test_a_consistently_better_system_is_called_different_only_with_three_rounds() -> None:
    tool = _load("ablation_fusion_eval")
    qrels, better, worse = _systems()
    three = tool.contrasts(qrels, 2, {"b": ([better] * 3, [worse] * 3)})
    two = tool.contrasts(qrels, 2, {"b": ([better] * 2, [worse] * 2)})
    same = tool.contrasts(qrels, 2, {"s": ([better] * 3, [better] * 3)})

    assert three["b"]["ndcg@10"]["different"] and three["b"]["mrr"]["different"]
    assert three["b"]["ndcg@10"]["mean_gap"] > 0 and three["b"]["ndcg@10"]["relative_gap"] > 0
    assert not two["b"]["ndcg@10"]["different"]  # 兩輪不下結論
    raw, adjusted = (
        three["b"]["ndcg@10"]["p_raw_by_round"],
        three["b"]["ndcg@10"]["p_holm_by_round"],
    )
    assert len(raw) == 3 and all(r <= a for r, a in zip(raw, adjusted, strict=True))
    assert not same["s"]["ndcg@10"]["different"] and same["s"]["ndcg@10"]["mean_gap"] == 0


def test_a_gap_smaller_than_the_spread_between_rounds_is_not_called_different() -> None:
    tool = _load("ablation_fusion_eval")
    qrels, better, worse = _systems()
    wobble = {q: ([*worse[q][:3], worse[q][3]] if q != "q0" else better[q]) for q in qrels}
    # 左系統三輪裡有一輪多對一題：輪間範圍大過它與右系統的平均差
    out = tool.contrasts(qrels, 2, {"w": ([worse, worse, wobble], [worse, worse, worse])})
    assert out["w"]["ndcg@10"]["between_round_range"] > abs(out["w"]["ndcg@10"]["mean_gap"])
    assert not out["w"]["ndcg@10"]["different"]


def test_only_restricts_the_grid_to_the_named_cells(tmp_path: Path) -> None:
    tool = _load("ablation_fusion_eval")
    args = _grid_args(tmp_path, [_arms(tmp_path / "a.json")])
    args.only = ["P50-k60", "semantic-only"]
    tool.cmd_grid(args)
    report: dict[str, Any] = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert list(report["cells"]) == ["P50-k60", "semantic-only"]
    assert list(report["versus_baseline"]) == ["semantic-only"]
    args.only = ["nope"]
    with pytest.raises(SystemExit, match="不存在"):
        tool.cmd_grid(args)


def test_bucket_scores_are_listed_per_bucket_with_their_question_counts() -> None:
    tool = _load("ablation_fusion_eval")
    qrels = {"a": {"d": 2}, "b": {"d": 2}, "c": {"d": 2}}
    ranking = {"a": ["d"], "b": ["x", "d"], "c": ["x"]}
    out = tool.by_bucket(qrels, ranking, {"a": "exact_id", "b": "paraphrase", "c": "paraphrase"}, 2)
    assert out["exact_id"]["n"] == 1 and out["exact_id"]["mrr"] == 1.0
    assert out["paraphrase"]["n"] == 2 and out["paraphrase"]["mrr"] == 0.25


# --- 下界落差 ----------------------------------------------------------------


def _report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"arms": {"hybrid": {"results": rows}}}


def _row(
    qid: str, status: str, *, deep: float, top10: float | None, sem: tuple[Any, Any] = (None, None)
) -> dict[str, Any]:
    return {
        "id": qid,
        "bucket": "negative" if status == "negative" else "exact_id",
        "status": status,
        "top_lexical": deep,
        "top_semantic": sem[0],
        "top10_lexical": top10,
        "top10_semantic": sem[1],
    }


def test_a_question_that_passes_on_k_hits_and_fails_on_ten_is_the_truncation_gap() -> None:
    tool = _load("threshold_calibration")
    floors = tool.Floors(lexical=0.5, semantic=0.71)
    qs, unmapped = tool.questions_of(
        _report(
            [
                _row("kept", "scored", deep=1.0, top10=1.0),
                _row("lost", "scored", deep=0.6, top10=0.2),
                _row("lost-by-semantic", "scored", deep=0.1, top10=0.1, sem=(0.8, 0.6)),
                _row("never", "scored", deep=0.2, top10=0.2),
                _row("leak", "negative", deep=0.9, top10=0.3),
                {"id": "gone", "bucket": "paraphrase", "status": "unmapped"},
            ]
        ),
        "hybrid",
    )
    assert unmapped == 1
    out = tool.analyze_truncation_gap(qs, floors)
    assert out["answerable"]["pass_deep"] == 3 and out["answerable"]["pass_top10"] == 1
    assert out["answerable"]["truncation_gap"] == 2
    assert out["answerable"]["gap_questions"] == ["lost", "lost-by-semantic"]
    assert out["unanswerable"]["truncation_gap"] == 1


def test_a_report_without_the_ten_hit_scores_is_refused_rather_than_read_as_no_gap() -> None:
    tool = _load("threshold_calibration")
    with pytest.raises(ValueError, match="top10_lexical"):
        tool.questions_of(_report([_row("old", "scored", deep=1.0, top10=None)]), "hybrid")
    with pytest.raises(ValueError, match="arms"):
        tool.questions_of({"results": []}, "hybrid")


def test_ten_hit_scores_above_the_k_hit_scores_are_impossible_and_refused() -> None:
    tool = _load("threshold_calibration")
    with pytest.raises(ValueError, match="不可能"):
        tool.questions_of(_report([_row("x", "scored", deep=0.2, top10=0.9)]), "hybrid")
    with pytest.raises(ValueError, match="不可能"):
        tool.questions_of(
            _report([_row("x", "scored", deep=0.2, top10=0.2, sem=(None, 0.8))]), "hybrid"
        )


def test_the_floors_are_read_from_consults_settings_and_missing_keys_stop_the_run(
    tmp_path: Path,
) -> None:
    tool = _load("threshold_calibration")
    floors = tool.read_floors(tool.CONSULT_SETTINGS)
    assert floors.lexical > 0 and 0 < floors.semantic < 1
    bad = tmp_path / "appsettings.json"
    bad.write_text(json.dumps({"Relevance": {"LexicalFloor": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="SemanticFloor"):
        tool.read_floors(bad)
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Relevance"):
        tool.read_floors(bad)


def test_the_grid_counts_coverage_and_leak_at_the_chosen_depth() -> None:
    tool = _load("threshold_calibration")
    qs, _ = tool.questions_of(
        _report(
            [
                _row("a", "scored", deep=2.0, top10=0.4),
                _row("b", "scored", deep=2.0, top10=2.0),
                _row("n", "negative", deep=0.9, top10=0.9),
            ]
        ),
        "hybrid",
    )
    top10 = tool.calibrate_thresholds(qs, depth="top10", lexical_grid=(0.5,), semantic_grid=(0.9,))
    deep = tool.calibrate_thresholds(qs, depth="deep", lexical_grid=(0.5,), semantic_grid=(0.9,))
    assert (top10[0]["answerable_pass"], top10[0]["unanswerable_pass"]) == (1, 1)
    assert (deep[0]["answerable_pass"], deep[0]["unanswerable_pass"]) == (2, 1)
    with pytest.raises(ValueError, match="depth"):
        tool.calibrate_thresholds(qs, depth="top50")


def test_wilson_interval_brackets_the_proportion() -> None:
    tool = _load("threshold_calibration")
    low, high = tool.wilson(2, 20)
    assert low < 0.1 < high
    assert tool.wilson(0, 0) is None
