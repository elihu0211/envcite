"""The golden test collection: sealed documents, the stratified split, ranx metrics, holes and the
model judgements merged into qrels."""

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


def test_ranx_scores_follow_the_ranking_and_a_grade_below_the_threshold_is_not_relevant() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 2, "B0000001-art-1": 1}, "q2": {"C0000001-art-1": 3}}
    ranking = {"q1": ["B0000001-art-1", "A0000001-art-1"], "q2": ["X0000001-art-1"]}

    got = gs.evaluate(qrels, ranking, ["hit_rate@1", "hit_rate@2", "mrr"], relevant_from=2)
    assert got == {"hit_rate@1": 0.0, "hit_rate@2": 0.5, "mrr": 0.25}
    # 交了空排序或沒交的題以 0 計，不從分母消失
    got = gs.evaluate(qrels, {"q1": ["A0000001-art-1"], "q2": []}, ["hit_rate@1"], relevant_from=2)
    assert got == {"hit_rate@1": 0.5}
    # 門檻是 1 時分數 1 的條也算相關
    got = gs.evaluate(qrels, ranking, ["hit_rate@1"], relevant_from=1)
    assert got == {"hit_rate@1": 0.5}


def test_ndcg_takes_the_grades_as_gains() -> None:
    gs = _tool()
    qrels = {"q1": {"A0000001-art-1": 3, "B0000001-art-1": 1}}
    best = gs.evaluate(
        qrels, {"q1": ["A0000001-art-1", "B0000001-art-1"]}, ["ndcg@10"], relevant_from=2
    )
    worse = gs.evaluate(
        qrels, {"q1": ["B0000001-art-1", "A0000001-art-1"]}, ["ndcg@10"], relevant_from=2
    )
    assert best == {"ndcg@10": 1.0}
    assert 0 < worse["ndcg@10"] < 1
    # 只有分數 1 的題沒有相關條，不計分
    assert gs.evaluate({"q1": {"A0000001-art-1": 1}}, {"q1": []}, ["ndcg@10"], relevant_from=2) == {
        "ndcg@10": 0.0
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


def _doc(**kw: Any) -> dict[str, Any]:
    return {
        "qrels": {"q1": {"A0000001-art-1": 2}},
        "upstream": {"q1": ["A0000001-art-1"]},
        "graded_by": {},
        **kw,
    }


def test_model_grades_are_merged_and_an_expected_article_is_only_raised() -> None:
    gs = _tool()
    doc = _doc()
    rows = [
        {"qid": "q1", "doc": "B0000001-art-1", "grade": 0},
        {"qid": "q1", "doc": "C0000001-art-1", "grade": 3},
        {"qid": "q1", "doc": "D0000001-art-1", "grade": None},
        {"qid": "q1", "doc": "A0000001-art-1", "grade": 1},
    ]

    merged, by, added = gs.merge_judgements(doc, rows, "t1")
    assert added == 3
    assert merged == {"q1": {"A0000001-art-1": 2, "B0000001-art-1": 0, "C0000001-art-1": 3}}
    assert by == {"q1": {"A0000001-art-1": "t1", "B0000001-art-1": "t1", "C0000001-art-1": "t1"}}
    assert doc["qrels"] == {"q1": {"A0000001-art-1": 2}}  # 原 qrels 不被改動

    raised, _, _ = gs.merge_judgements(
        doc, [{"qid": "q1", "doc": "A0000001-art-1", "grade": 3}], "t"
    )
    assert raised["q1"]["A0000001-art-1"] == 3


def test_a_second_judgement_of_the_same_article_must_agree() -> None:
    gs = _tool()
    doc = _doc(
        qrels={"q1": {"A0000001-art-1": 2, "B0000001-art-1": 1}},
        graded_by={"q1": {"B0000001-art-1": "t1"}},
    )
    same, _, added = gs.merge_judgements(
        doc, [{"qid": "q1", "doc": "B0000001-art-1", "grade": 1}], "t2"
    )
    assert added == 0 and same == doc["qrels"]
    with pytest.raises(ValueError, match="已判為 1"):
        gs.merge_judgements(doc, [{"qid": "q1", "doc": "B0000001-art-1", "grade": 2}], "t2")


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"qid": "neg", "doc": "A0000001-art-1", "grade": 1}], "沒有這一題"),
        ([{"qid": "q1", "doc": "B0000001-art-1", "grade": 4}], "0–3"),
        ([{"qid": "q1", "doc": "B0000001-art-1", "grade": True}], "0–3"),
    ],
)
def test_a_judgement_file_with_a_bad_row_is_refused_whole(
    rows: list[dict[str, Any]], message: str
) -> None:
    gs = _tool()
    with pytest.raises(ValueError, match=message):
        gs.merge_judgements(_doc(), rows, "t")


def test_dual_track_composite_natural_keys_in_golden_set() -> None:
    gs = _tool()
    # 中央法規的條文代號沒有管轄前綴；管轄的值是資料庫的 'TW-CENTRAL'
    assert gs.CENTRAL == "TW-CENTRAL"
    assert gs.doc_id("O0020001", "15-1") == "O0020001-art-15-1"
    assert gs.doc_id("O0020001", "15-1", gs.CENTRAL) == "O0020001-art-15-1"
    assert gs.parse_doc("O0020001-art-15-1") == ("O0020001", "15-1")
    assert gs.parse_doc_key("O0020001-art-15-1") == (gs.CENTRAL, "O0020001", "15-1")
    assert gs.parse_doc_key(gs.doc_id("O0020001", "15-1", gs.CENTRAL)) == (
        gs.CENTRAL,
        "O0020001",
        "15-1",
    )

    # 地方自治法規
    assert gs.doc_id("01-02-1001", "5", "TW-TPE") == "TW-TPE/01-02-1001-art-5"
    assert gs.parse_doc("TW-TPE/01-02-1001-art-5") == ("01-02-1001", "5")
    assert gs.parse_doc_key("TW-TPE/01-02-1001-art-5") == ("TW-TPE", "01-02-1001", "5")

    # 混合評估
    qrels = {
        "q_central": {"O0020001-art-15-1": 3},
        "q_local": {"TW-TPE/01-02-1001-art-5": 2},
    }
    ranking = {
        "q_central": ["O0020001-art-15-1"],
        "q_local": ["TW-TPE/01-02-1001-art-5"],
    }
    scores = gs.evaluate(qrels, ranking, ["hit_rate@1", "mrr", "ndcg@10"], relevant_from=2)
    assert scores == {"hit_rate@1": 1.0, "mrr": 1.0, "ndcg@10": 1.0}
