"""The golden-set retrieval tool: mapping a question's expected articles to the corpus, and
scoring a hit as any expected article within the top k."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

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


def test_a_hit_is_any_expected_article_in_the_top_k_and_summaries_split_by_bucket() -> None:
    tool = _tool()
    by_title, provisions = _corpus(tool)
    items = [
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
            "citation_allowlist": [{"statute": "空氣污染防制法", "article": "第24條"}],
        },
        {"id": "neg", "bucket": "negative", "question": "q"},
    ]
    store = FakeStore([("O0020001", "68"), ("Z", "1"), ("O0020001", "15-1")])
    out = tool.search_all(
        store,
        FakeSegmenter(),
        tool.resolve(items, by_title, provisions),
        None,
        k=50,
        on="2026-10-23",
    )
    got = {r.id: r for r in out}

    assert got["first"].rank == 1
    assert (
        got["second-of-two"].rank == 3 and got["second-of-two"].hit_article == "O0020001 第15-1條"
    )
    assert got["miss"].rank is None
    assert got["neg"].rank is None and got["neg"].top_lexical == 10.0

    s = tool.summarize(out)
    assert s["by_bucket"]["exact_id"]["recall@1"] == 1.0
    assert s["by_bucket"]["multi_hop"]["recall@1"] == 0.0
    assert s["by_bucket"]["multi_hop"]["recall@5"] == 1.0
    assert s["by_bucket"]["paraphrase"]["mrr"] == 0.0
    assert s["overall"]["n"] == 3  # negative 不計分
    assert s["overall"]["recall@5"] == round(2 / 3, 4)
    assert s["negative"]["n"] == 1 and s["negative"]["top_semantic"] is None
