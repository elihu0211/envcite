"""The golden test collection: sealed documents, the stratified split, ranx metrics, holes and the
judgement pool."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "golden_set.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_set", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_set"] = module
    spec.loader.exec_module(module)
    return module


def test_a_sealed_document_that_was_edited_is_refused(tmp_path: Path) -> None:
    gs = _tool()
    path = tmp_path / "qrels.json"
    doc = gs.write(path, {"golden_sha256": "g", "qrels": {"q": {"A0000001-art-1": 1}}})
    assert gs.read(path) == doc

    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["qrels"]["q"]["A0000001-art-2"] = 1
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(SystemExit, match="sha256"):
        gs.read(path)


def test_the_split_is_stratified_by_bucket_and_fixed_by_its_seed() -> None:
    gs = _tool()
    items = [
        {"id": f"{b}-{i:02d}", "bucket": b} for b in ("exact_id", "negative") for i in range(10)
    ]
    items += [{"id": f"multi_hop-{i}", "bucket": "multi_hop"} for i in range(3)]

    a = gs.split(items, seed="s", test_fraction=0.5)
    assert a == gs.split(list(reversed(items)), seed="s", test_fraction=0.5)
    assert a != gs.split(items, seed="other", test_fraction=0.5)
    assert sorted(a["dev"] + a["test"]) == sorted(i["id"] for i in items)
    assert not set(a["dev"]) & set(a["test"])
    for bucket, n in (("exact_id", 5), ("negative", 5), ("multi_hop", 2)):
        assert sum(q.startswith(bucket) for q in a["test"]) == n


def test_ranx_scores_follow_the_ranking_and_a_judged_zero_is_not_relevant() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 1, "B0000001-art-1": 0}, "q2": {"C0000001-art-1": 1}}
    ranking = {"q1": ["B0000001-art-1", "A0000001-art-1"], "q2": ["X0000001-art-1"]}

    got = gs.evaluate(qrels, ranking, ["hit_rate@1", "hit_rate@2", "mrr"])
    assert got == {"hit_rate@1": 0.0, "hit_rate@2": 0.5, "mrr": 0.25}
    # 交了空排序或沒交的題以 0 計，不從分母消失
    assert gs.evaluate(qrels, {"q1": ["A0000001-art-1"], "q2": []}, ["hit_rate@1"]) == {
        "hit_rate@1": 0.5
    }


def test_holes_are_the_unjudged_articles_of_the_top_ten() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 1, "B0000001-art-1": 0}}
    docs = ["B0000001-art-1", "A0000001-art-1"] + [f"Z0000001-art-{i}" for i in range(1, 12)]
    ranking = {"q1": docs}

    assert gs.holes(qrels, ranking) == {"q1": [f"Z0000001-art-{i}" for i in range(1, 9)]}
    assert gs.hole_rate(qrels, ranking) == 0.8
    assert gs.hole_rate(qrels, {"q1": []}) is None


def test_the_pool_merges_the_holes_of_every_run_once() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 1}}
    a = {"q1": ["A0000001-art-1", "B0000001-art-1"]}
    b = {"q1": ["B0000001-art-1", "C0000001-art-1"], "neg": ["D0000001-art-1"]}

    assert gs.pool_rows(qrels, [a, b]) == [("q1", "B0000001-art-1"), ("q1", "C0000001-art-1")]


def _judged(**kw: Any) -> dict[str, Any]:
    return {"judge": "王小明", "date": "2026-11-03", **kw}


def test_judgements_are_merged_by_relevance_and_a_prescreen_is_never_read() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 1}}
    items = [
        {"qid": "q1", "doc": "B0000001-art-1", "relevance": 0, "prescreen": 1},
        {"qid": "q1", "doc": "C0000001-art-1", "relevance": 1},
        {"qid": "q1", "doc": "D0000001-art-1", "relevance": None, "prescreen": 1},
        {"qid": "q1", "doc": "A0000001-art-1", "relevance": 1},
    ]

    merged, added = gs.merge_judgements(qrels, _judged(items=items))
    assert added == 2
    assert merged == {"q1": {"A0000001-art-1": 1, "B0000001-art-1": 0, "C0000001-art-1": 1}}
    assert qrels == {"q1": {"A0000001-art-1": 1}}  # 原 qrels 不被改動


@pytest.mark.parametrize(
    ("judged", "message"),
    [
        (_judged(judge=""), "判定者"),
        (_judged(date="昨天"), "ISO 8601"),
        (_judged(items=[{"qid": "q1", "doc": "A0000001-art-1", "relevance": 0}]), "已判為 1"),
        (_judged(items=[{"qid": "neg", "doc": "A0000001-art-1", "relevance": 1}]), "沒有這一題"),
        (_judged(items=[{"qid": "q1", "doc": "B0000001-art-1", "relevance": 2}]), "0 或 1"),
        (_judged(items=[{"qid": "q1", "doc": "B0000001-art-1", "relevance": True}]), "0 或 1"),
    ],
)
def test_a_judgement_file_that_is_unsigned_or_contradicts_the_qrels_is_refused(
    judged: dict[str, Any], message: str
) -> None:
    gs = _tool()
    with pytest.raises(ValueError, match=message):
        gs.merge_judgements({"q1": {"A0000001-art-1": 1}}, judged)
