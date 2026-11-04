"""The golden-set retrieval tool: mapping a question's expected articles to the corpus, and
scoring a hit as any expected article within the top k."""

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "golden_retrieval_eval.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_retrieval_eval", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_retrieval_eval"] = module
    spec.loader.exec_module(module)
    return module


def _corpus(tool: ModuleType) -> tuple[dict[str, set[str]], dict[tuple[str, str], Any]]:
    by_title = {"空氣污染防制法": {"O0020001"}, "重名法": {"X0000001", "X0000002"}}
    provisions = {
        ("O0020001", n): tool.Provision("O0020001", n, "art_" + n, "空氣污染防制法")
        for n in ("68", "15-1", "24")
    }
    return by_title, provisions


def test_article_number_reads_the_forms_the_set_uses() -> None:
    tool = _tool()
    assert tool.article_number("第68條") == "68"
    assert tool.article_number("第15條之1") == "15-1"
    assert tool.article_number("第46-1條") == "46-1"
    assert tool.article_number("第38條第2項") == "38"
    assert tool.article_number("第 7 條") == "7"
    assert tool.article_number(None) is None
    assert tool.article_number("附表一") is None


def test_alternatives_are_flattened_into_expected_articles() -> None:
    tool = _tool()
    item = {
        "citation_allowlist": [
            {"statute": "甲法", "article": "第1條"},
            {"alternatives": [{"statute": "乙法", "article": "第2條"}, {"statute": "丙法"}]},
        ]
    }
    assert [(e.statute, e.article) for e in tool.expected_of(item)] == [
        ("甲法", "第1條"),
        ("乙法", "第2條"),
        ("丙法", ""),
    ]


def test_mapping_prefers_the_article_id_and_names_each_reason_it_fails() -> None:
    tool = _tool()
    by_title, provisions = _corpus(tool)

    def m(**kw: Any) -> tuple[Any, str]:
        found: tuple[Any, str] = tool.map_expected(tool.Expected(**kw), by_title, provisions)
        return found

    assert m(statute="空氣污染防制法", article="第68條")[0].article_path == "art_68"
    assert (
        m(statute="任意名稱", article="第1條", article_id="O0020001-art-15-1")[0].number == "15-1"
    )
    assert "沒有完全相同的標題" in m(statute="空氣汙染防制法", article="第68條")[1]
    assert "多個 pcode" in m(statute="重名法", article="第1條")[1]
    assert "條號無法解析" in m(statute="空氣污染防制法", article="附表")[1]
    assert "沒有這一條" in m(statute="空氣污染防制法", article="第99條")[1]
    assert "格式不合" in m(statute="空氣污染防制法", article="第1條", article_id="bad")[1]


def test_unmapped_questions_are_set_aside_and_negatives_are_not_scored() -> None:
    tool = _tool()
    by_title, provisions = _corpus(tool)
    items = [
        {
            "id": "ok",
            "bucket": "exact_id",
            "question": "q",
            "citation_allowlist": [
                {"statute": "空氣污染防制法", "article": "第68條"},
                {"statute": "不存在法", "article": "第1條"},
            ],
        },
        {"id": "none", "bucket": "paraphrase", "question": "q", "citation_allowlist": []},
        {
            "id": "gone",
            "bucket": "multi_hop",
            "question": "q",
            "citation_allowlist": [{"statute": "不存在法", "article": "第1條"}],
        },
        {"id": "neg", "bucket": "negative", "question": "q"},
    ]
    got = {r.id: r for r in tool.resolve(items, by_title, provisions)}

    assert [got[i].status for i in ("ok", "none", "gone", "neg")] == [
        "scored",
        "unmapped",
        "unmapped",
        "negative",
    ]
    assert len(got["ok"].mapped) == 1 and len(got["ok"].unmapped_reasons) == 1
    assert got["none"].unmapped_reasons == ["題目沒有期望條文"]


class FakeStore:
    def __init__(self, hits: list[tuple[str, str]]) -> None:
        self.hits = hits

    def search(self, terms: str, **kw: Any) -> list[Any]:
        return [
            SimpleNamespace(law_id=p, number=n, lexical_score=10.0 - i, semantic_similarity=None)
            for i, (p, n) in enumerate(self.hits[: kw["limit"]])
        ]


class FakeSegmenter:
    tokenizer_id = "fake"

    def to_tsvector_input(self, text: str) -> str:
        return text


ITEMS: list[dict[str, Any]] = [
    {
        "id": "first",
        "bucket": "exact_id",
        "question": "q",
        "citation_allowlist": [{"statute": "空氣污染防制法", "article": "第68條"}],
    },
    {
        "id": "second-of-two",
        "bucket": "multi_hop",
        "question": "q",
        "citation_allowlist": [
            {"statute": "空氣污染防制法", "article": "第24條"},
            {"statute": "空氣污染防制法", "article": "第15條之1"},
        ],
    },
    {
        "id": "miss",
        "bucket": "paraphrase",
        "question": "q",
        "citation_allowlist": [
            {"statute": "空氣污染防制法", "article": "第24條"},
            {"statute": "不存在法", "article": "第1條"},
        ],
    },
    {
        "id": "gone",
        "bucket": "paraphrase",
        "question": "q",
        "citation_allowlist": [{"statute": "不存在法", "article": "第1條"}],
    },
    {"id": "neg", "bucket": "negative", "question": "q"},
]


def _qrels_doc(tool: ModuleType) -> dict[str, Any]:
    qrels, unmapped = tool.build_qrels(tool.resolve(ITEMS, *_corpus(tool)))
    return {"qrels": qrels, "unmapped": unmapped, "relevant_from": 2}


def test_qrels_hold_the_mapped_articles_of_answerable_questions_only() -> None:
    tool = _tool()
    doc = _qrels_doc(tool)

    assert doc["qrels"] == {
        "first": {"O0020001-art-68": 2},
        "second-of-two": {"O0020001-art-24": 2, "O0020001-art-15-1": 2},
        "miss": {"O0020001-art-24": 2},
    }
    assert set(doc["unmapped"]) == {"miss", "gone"}  # 「miss」只是部分對應


def _measure(tool: ModuleType, doc: dict[str, Any], hits: list[tuple[str, str]]) -> list[Any]:
    rows = tool.questions(ITEMS, doc, frozenset(i["id"] for i in ITEMS))
    return list(
        tool.search_all(
            FakeStore(hits),
            FakeSegmenter(),
            rows,
            None,
            qrels=doc["qrels"],
            relevant_from=2,
            k=50,
            on="2026-10-23",
        )
    )


def test_a_hit_is_any_relevant_article_in_the_top_k_and_ranx_scores_it_by_bucket() -> None:
    tool = _tool()
    doc = _qrels_doc(tool)
    out = _measure(tool, doc, [("O0020001", "68"), ("Z0000001", "1"), ("O0020001", "15-1")])
    got = {r.id: r for r in out}

    assert got["first"].rank == 1
    assert got["second-of-two"].rank == 3
    assert got["second-of-two"].ranking == [
        "O0020001-art-68",
        "Z0000001-art-1",
        "O0020001-art-15-1",
    ]
    assert got["miss"].rank is None and got["miss"].partial
    assert got["gone"].status == "unmapped" and got["gone"].ranking == []
    assert got["neg"].ranking == [] and got["neg"].top_lexical == 10.0

    s = tool.summarize(out, doc["qrels"], 2)
    assert s["by_bucket"]["exact_id"]["hit_rate@1"] == 1.0
    assert s["by_bucket"]["multi_hop"]["hit_rate@1"] == 0.0
    assert s["by_bucket"]["multi_hop"]["hit_rate@5"] == 1.0
    assert s["by_bucket"]["multi_hop"]["mrr"] == round(1 / 3, 4)
    assert s["by_bucket"]["paraphrase"]["unmapped"] == 1
    assert s["by_bucket"]["paraphrase"]["partially_mapped"] == 1
    assert s["overall"]["n"] == 3  # negative 與 unmapped 不計分
    assert s["overall"]["hit_rate@5"] == round(2 / 3, 4)
    # 每題前 10 條都是同樣三條；只有各題 qrels 裡的才算判定過
    assert s["overall"]["hole@10"] == round((2 + 2 + 3) / 9, 4)
    assert s["negative"]["n"] == 1 and s["negative"]["top_semantic"] is None


def test_an_article_graded_below_the_threshold_is_not_a_hole_and_not_a_hit() -> None:
    tool = _tool()
    doc = _qrels_doc(tool)
    doc["qrels"]["first"]["Z0000001-art-1"] = 1
    out = _measure(tool, doc, [("Z0000001", "1"), ("O0020001", "68")])
    s = tool.summarize([r for r in out if r.id == "first"], doc["qrels"], 2)

    assert s["by_bucket"]["exact_id"]["hit_rate@1"] == 0.0
    assert s["by_bucket"]["exact_id"]["hit_rate@5"] == 1.0
    assert s["by_bucket"]["exact_id"]["hole@10"] == 0.0


def test_only_the_questions_of_the_subset_are_measured() -> None:
    tool = _tool()
    rows = tool.questions(ITEMS, _qrels_doc(tool), frozenset({"first", "neg"}))
    assert [r.id for r in rows] == ["first", "neg"]


def test_a_scope_drops_hits_of_other_laws_before_the_first_k_are_taken() -> None:
    tool = _tool()
    doc = _qrels_doc(tool)
    rows = tool.questions(ITEMS[:1], doc, frozenset({"first"}))
    store = FakeStore([("Z0000001", str(i)) for i in range(1, 6)] + [("O0020001", "68")])
    kw: dict[str, Any] = {"qrels": doc["qrels"], "relevant_from": 2, "k": 3, "on": "2026-10-23"}

    plain = tool.search_all(store, FakeSegmenter(), rows, None, **kw)
    scoped = tool.search_all(
        store, FakeSegmenter(), rows, None, scope=frozenset({"O0020001"}), **kw
    )

    assert plain[0].rank is None and plain[0].out_of_scope_dropped is None
    assert scoped[0].rank == 1 and scoped[0].out_of_scope_dropped == 5


def test_a_published_row_keeps_the_question_hash_but_not_its_text_or_ranking() -> None:
    tool = _tool()
    row = tool.publishable(tool.Measured("x", "exact_id", "某題目文字", "scored", ranking=["a"]))
    assert "question" not in row and "ranking" not in row
    assert row["question_sha256"] == hashlib.sha256("某題目文字".encode()).hexdigest()


def _run(tool: ModuleType, ranking: dict[str, list[str]], subset: str = "dev") -> dict[str, Any]:
    gs = sys.modules["golden_set"]
    sealed: dict[str, Any] = gs.seal(
        {"golden_sha256": "g", "split_sha256": "s", "subset": subset, "ranking": ranking}
    )
    return sealed


def test_runs_are_compared_with_a_paired_test_and_must_share_a_subset() -> None:
    tool = _tool()
    gs = sys.modules["golden_set"]
    qrels_doc = gs.seal(
        {
            "golden_sha256": "g",
            "relevant_from": 2,
            "qrels": {f"q{i}": {"A0000001-art-1": 3} for i in range(8)},
        }
    )
    good = {f"q{i}": ["A0000001-art-1"] for i in range(8)}
    bad = {f"q{i}": ["B0000001-art-1", "A0000001-art-1"] for i in range(8)}

    c = tool.compared(qrels_doc, {"good": _run(tool, good), "bad": _run(tool, bad)}, 0.05)
    r1 = next(p for p in c["pairs"] if p["metric"] == "hit_rate@1")
    assert r1["difference"] == -1.0 and r1["significant"]
    ndcg = next(p for p in c["pairs"] if p["metric"] == "ndcg@10")
    assert ndcg["difference"] < 0
    assert c["runs"]["bad"]["hole@10"] == 0.5
    same = tool.compared(qrels_doc, {"a": _run(tool, good), "b": _run(tool, good)}, 0.05)
    assert not any(p["significant"] for p in same["pairs"])

    with pytest.raises(SystemExit, match="切分子集"):
        tool.compared(qrels_doc, {"a": _run(tool, good), "b": _run(tool, good, "test")}, 0.05)
