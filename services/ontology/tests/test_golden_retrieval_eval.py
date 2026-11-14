"""The golden-set retrieval tool: mapping a question's expected articles to the corpus, and
scoring a hit as any expected article within the top k."""

import hashlib
import importlib.util
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

from envcite_ontology import projection
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_ontology.store.records import SearchHit
from envcite_segmenter import Segmenter
from tests.sample import AIR, WATER

TOOL = Path(__file__).resolve().parents[1] / "tools" / "golden_retrieval_eval.py"

Conn = psycopg.Connection[tuple[object, ...]]
ON = "2026-10-23"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_retrieval_eval", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_retrieval_eval"] = module
    spec.loader.exec_module(module)
    return module


def _add_law(
    conn: Conn,
    *,
    jurisdiction: str,
    scheme: str,
    local_id: str,
    kind: str,
    title: str,
    articles: dict[str, str],
) -> None:
    """一部法規，直接寫進真實的資料表（與 `tests/test_search.py` 同形）。"""
    law = conn.execute(
        "INSERT INTO corpus.legal_resource (jurisdiction, id_scheme, local_id, norm_kind,"
        " document_type) VALUES (%s, %s, %s, %s, '法規') RETURNING id",
        (jurisdiction, scheme, local_id, kind),
    ).fetchone()
    assert law is not None
    expression = conn.execute(
        "INSERT INTO corpus.legal_expression"
        " (resource_id, language, version_date, title, text_period, text_status, text_authority)"
        " VALUES (%s, 'zho', DATE '2020-01-01', %s, daterange(DATE '2020-01-01', NULL),"
        " 'available', 'official_db') RETURNING id",
        (law[0], title),
    ).fetchone()
    assert expression is not None
    for number, content in articles.items():
        conn.execute(
            """
            WITH p AS (
                INSERT INTO corpus.provision (resource_id, provision_type, path)
                VALUES (%(law)s, 'article', 'art_' || %(n)s)
                RETURNING id
            )
            INSERT INTO corpus.provision_version
                    (provision_id, expression_id, resource_id, content,
                     force_period, commencement_rule)
            SELECT p.id, %(expr)s, %(law)s, %(content)s, daterange(DATE '2020-01-03', NULL),
                   'promulgation'
              FROM p
            """,
            {"law": law[0], "expr": expression[0], "n": number, "content": content},
        )


#: 重名的兩部中央法規（標題路徑不得猜）、一部地方自治條例（管轄不是中央）。
DUP_A, DUP_B, TPE = "X0000001", "X0000002", "01-02-1001"


@pytest.fixture
def corpus(conn: Conn, sample: None) -> tuple[ModuleType, Any, Any]:
    """匯入器匯入的空污法與水污法，加上直接寫入的重名法與地方條例；以工具的 `load_corpus` 讀。"""
    five = {str(n): f"重名法第{n}條" for n in range(1, 6)}
    for local_id in (DUP_A, DUP_B):
        _add_law(
            conn,
            jurisdiction="TW-CENTRAL",
            scheme="moj_pcode",
            local_id=local_id,
            kind="statute",
            title="重名法",
            articles=five,
        )
    _add_law(
        conn,
        jurisdiction="TW-TPE",
        scheme="local_glrs",
        local_id=TPE,
        kind="local_ordinance",
        title="臺北市空氣污染防制自治條例",
        articles={"5": "於臺北市特定區域焚燒廢棄物者，處罰鍰。"},
    )
    conn.commit()
    tool = _tool()
    by_title, provisions = tool.load_corpus(conn)
    return tool, by_title, provisions


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


def test_central_laws_are_keyed_by_the_jurisdiction_the_database_gives_them(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    _, by_title, provisions = corpus
    gs = sys.modules["golden_set"]

    assert by_title["空氣污染防制法"] == {(gs.CENTRAL, AIR)}
    assert provisions[(gs.CENTRAL, AIR, "100")].jurisdiction == gs.CENTRAL
    assert provisions[("TW-TPE", TPE, "5")].jurisdiction == "TW-TPE"
    # 管轄只有一種寫法：匯入器寫的中央法規沒有別的管轄
    assert {j for j, _, _ in provisions} == {gs.CENTRAL, "TW-TPE"}


def test_mapping_prefers_the_article_id_and_names_each_reason_it_fails(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    tool, by_title, provisions = corpus

    def m(**kw: Any) -> tuple[Any, str]:
        found: tuple[Any, str] = tool.map_expected(tool.Expected(**kw), by_title, provisions)
        return found

    by_name = m(statute="空氣污染防制法", article="第100條")[0]
    assert (by_name.pcode, by_name.number, by_name.article_path) == (AIR, "100", "art_100")
    assert m(statute="任意名稱", article="第1條", article_id=f"{WATER}-art-40")[0].number == "40"
    local = m(statute="臺北市空氣污染防制自治條例", article="第5條")[0]
    assert (local.jurisdiction, local.pcode) == ("TW-TPE", TPE)
    assert m(statute="x", article="x", article_id=f"TW-TPE/{TPE}-art-5")[0] == local
    assert "沒有完全相同的標題" in m(statute="空氣汙染防制法", article="第1條")[1]
    assert "多部法規" in m(statute="重名法", article="第1條")[1]
    assert "條號無法解析" in m(statute="空氣污染防制法", article="附表")[1]
    assert "沒有這一條" in m(statute="空氣污染防制法", article="第99條")[1]
    assert "沒有這一條：TW-TPE/" in m(statute="x", article="x", article_id=f"TW-TPE/{AIR}-art-1")[1]
    assert "格式不合" in m(statute="空氣污染防制法", article="第1條", article_id="bad")[1]


def test_unmapped_questions_are_set_aside_and_negatives_are_not_scored(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    tool, by_title, provisions = corpus
    items = [
        {
            "id": "ok",
            "bucket": "exact_id",
            "question": "q",
            "citation_allowlist": [
                {"statute": "空氣污染防制法", "article": "第1條"},
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


class FakeRetrieval:
    """檢索的替身，但回的是真的 `SearchHit`，身分欄位取自從資料庫讀到的條。"""

    def __init__(self, hits: Sequence[Any]) -> None:
        self.hits = hits

    def search(self, terms: str, **kw: Any) -> list[SearchHit]:
        return [
            SearchHit(
                iri="",
                jurisdiction=p.jurisdiction,
                id_scheme="moj_pcode",
                local_id=p.pcode,
                law_title=p.title,
                number=p.number,
                content="",
                lexical_score=10.0 - i,
            )
            for i, p in enumerate(self.hits[: kw["limit"]])
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
        "citation_allowlist": [{"statute": "空氣污染防制法", "article": "第1條"}],
    },
    {
        "id": "second-of-two",
        "bucket": "multi_hop",
        "question": "q",
        "citation_allowlist": [
            {"statute": "水污染防治法", "article": "第7條"},
            {"statute": "水污染防治法", "article": "第40條"},
        ],
    },
    {
        "id": "miss",
        "bucket": "paraphrase",
        "question": "q",
        "citation_allowlist": [
            {"statute": "空氣污染防制法", "article": "第100條"},
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


def _qrels_doc(corpus: tuple[ModuleType, Any, Any]) -> dict[str, Any]:
    tool, by_title, provisions = corpus
    qrels, unmapped = tool.build_qrels(tool.resolve(ITEMS, by_title, provisions))
    return {"qrels": qrels, "unmapped": unmapped, "relevant_from": 2}


def test_qrels_hold_the_mapped_articles_of_answerable_questions_only(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    doc = _qrels_doc(corpus)

    assert doc["qrels"] == {
        "first": {f"{AIR}-art-1": 2},
        "second-of-two": {f"{WATER}-art-7": 2, f"{WATER}-art-40": 2},
        "miss": {f"{AIR}-art-100": 2},
    }
    assert set(doc["unmapped"]) == {"miss", "gone"}  # 「miss」只是部分對應


def _measure(
    corpus: tuple[ModuleType, Any, Any], doc: dict[str, Any], hits: Sequence[tuple[str, str]]
) -> list[Any]:
    tool, _, provisions = corpus
    gs = sys.modules["golden_set"]
    rows = tool.questions(ITEMS, doc, frozenset(i["id"] for i in ITEMS))
    return list(
        tool.search_all(
            FakeRetrieval([provisions[(gs.CENTRAL, p, n)] for p, n in hits]),
            FakeSegmenter(),
            rows,
            None,
            qrels=doc["qrels"],
            relevant_from=2,
            k=50,
            on=ON,
        )
    )


def test_a_hit_is_any_relevant_article_in_the_top_k_and_ranx_scores_it_by_bucket(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    tool = corpus[0]
    doc = _qrels_doc(corpus)
    out = _measure(corpus, doc, [(AIR, "1"), (DUP_A, "1"), (WATER, "40")])
    got = {r.id: r for r in out}

    assert got["first"].rank == 1
    assert got["second-of-two"].rank == 3
    assert got["second-of-two"].ranking == [
        f"{AIR}-art-1",
        f"{DUP_A}-art-1",
        f"{WATER}-art-40",
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


def test_an_article_graded_below_the_threshold_is_not_a_hole_and_not_a_hit(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    tool = corpus[0]
    doc = _qrels_doc(corpus)
    doc["qrels"]["first"][f"{DUP_A}-art-1"] = 1
    out = _measure(corpus, doc, [(DUP_A, "1"), (AIR, "1")])
    s = tool.summarize([r for r in out if r.id == "first"], doc["qrels"], 2)

    assert s["by_bucket"]["exact_id"]["hit_rate@1"] == 0.0
    assert s["by_bucket"]["exact_id"]["hit_rate@5"] == 1.0
    assert s["by_bucket"]["exact_id"]["hole@10"] == 0.0


def test_only_the_questions_of_the_subset_are_measured(corpus: tuple[ModuleType, Any, Any]) -> None:
    tool = corpus[0]
    rows = tool.questions(ITEMS, _qrels_doc(corpus), frozenset({"first", "neg"}))
    assert [r.id for r in rows] == ["first", "neg"]


def test_a_scope_drops_hits_of_other_laws_before_the_first_k_are_taken(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    tool, _, provisions = corpus
    gs = sys.modules["golden_set"]
    doc = _qrels_doc(corpus)
    rows = tool.questions(ITEMS[:1], doc, frozenset({"first"}))
    store = FakeRetrieval(
        [provisions[(gs.CENTRAL, DUP_A, str(i))] for i in range(1, 6)]
        + [provisions[(gs.CENTRAL, AIR, "1")]]
    )
    kw: dict[str, Any] = {"qrels": doc["qrels"], "relevant_from": 2, "k": 3, "on": ON}

    plain = tool.search_all(store, FakeSegmenter(), rows, None, **kw)
    scoped = tool.search_all(store, FakeSegmenter(), rows, None, scope=frozenset({AIR}), **kw)

    assert plain[0].rank is None and plain[0].out_of_scope_dropped is None
    assert scoped[0].rank == 1 and scoped[0].out_of_scope_dropped == 5


def test_the_top_scores_are_kept_for_the_first_k_hits_and_for_the_ten_consult_reads(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    """分數隨名次升高、語意相似度只出現在第 13 條：前 10 條的最高分低於前 k 條的。"""
    tool, _, provisions = corpus
    doc = _qrels_doc(corpus)
    rows = tool.questions(ITEMS[:1], doc, frozenset({"first"}))

    class Rising(FakeRetrieval):
        def search(self, terms: str, **kw: Any) -> list[SearchHit]:
            hits = super().search(terms, **kw)
            return [
                replace(
                    h,
                    lexical_score=float(i),
                    semantic_similarity=0.9 if i == 12 else None,
                )
                for i, h in enumerate(hits)
            ]

    store = Rising(
        [provisions[(sys.modules["golden_set"].CENTRAL, DUP_A, str(i))] for i in (1, 2, 3, 4, 5)]
        * 3
    )
    got = tool.search_all(
        store, FakeSegmenter(), rows, None, qrels=doc["qrels"], relevant_from=2, k=15, on=ON
    )[0]

    assert tool.CONSULT_LIMIT == 10
    assert (got.top_lexical, got.top_semantic) == (14.0, 0.9)
    assert (got.top10_lexical, got.top10_semantic) == (9.0, None)


def test_a_real_search_hit_lands_on_the_qrels_doc_id_of_the_article_it_was_mapped_to(
    corpus: tuple[ModuleType, Any, Any], conn: Conn, store: OntologyStore
) -> None:
    """從資料庫讀對應、真實的混合檢索、命中轉條文代號、對 qrels：中央與地方各一題。"""
    tool, by_title, provisions = corpus
    projection.rebuild(conn)
    segmenter = Segmenter(store.retrieval.lexicon())
    items = [
        {
            "id": "central",
            "bucket": "exact_id",
            "question": "為防制空氣污染，維護生活環境及國民健康，以提高生活品質",
            "citation_allowlist": [{"statute": "空氣污染防制法", "article": "第1條"}],
        },
        {
            "id": "local",
            "bucket": "exact_id",
            "question": "於臺北市特定區域焚燒廢棄物者，處罰鍰",
            "citation_allowlist": [{"article_id": f"TW-TPE/{TPE}-art-5"}],
        },
    ]
    qrels, _ = tool.build_qrels(tool.resolve(items, by_title, provisions))
    assert qrels == {"central": {f"{AIR}-art-1": 2}, "local": {f"TW-TPE/{TPE}-art-5": 2}}

    doc = {"qrels": qrels, "unmapped": {}, "relevant_from": 2}
    rows = tool.questions(items, doc, frozenset({"central", "local"}))
    out = {
        r.id: r
        for r in tool.search_all(
            store.retrieval, segmenter, rows, None, qrels=qrels, relevant_from=2, k=10, on=ON
        )
    }

    assert out["central"].ranking[0] == f"{AIR}-art-1" and out["central"].rank == 1
    assert out["local"].ranking[0] == f"TW-TPE/{TPE}-art-5" and out["local"].rank == 1


def test_article_texts_reads_the_text_in_force_by_jurisdiction_and_local_id(
    corpus: tuple[ModuleType, Any, Any], conn: Conn
) -> None:
    """判定工具讀條文全文走這個查詢；管轄是列舉型別，與文字比較曾使整個查詢失敗。"""
    gs = sys.modules["golden_set"]
    got = gs.article_texts(
        conn, [gs.doc_id(AIR, "1"), gs.doc_id(TPE, "5", "TW-TPE"), gs.doc_id(AIR, "999")], ON
    )

    assert set(got) == {gs.doc_id(AIR, "1"), gs.doc_id(TPE, "5", "TW-TPE")}
    title, _, text = got[gs.doc_id(AIR, "1")]
    assert title == "空氣污染防制法" and "防制空氣污染" in text
    assert got[gs.doc_id(TPE, "5", "TW-TPE")][0] == "臺北市空氣污染防制自治條例"


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


class FixedReranker:
    """給定分數的精排替身：第 i 個候選得第 i 個分數，不看文字。"""

    model = "fixed"

    def __init__(self, scores: Sequence[float], fail: bool = False) -> None:
        self.scores = scores
        self.fail = fail
        self.asked: list[int] = []

    def score(
        self, query: str, documents: Sequence[str], *, timeout: float | None = None
    ) -> list[float]:
        self.asked.append(len(documents))
        if self.fail:
            raise RuntimeError("endpoint down")
        return list(self.scores[: len(documents)])


def _rerank_run(
    corpus: tuple[ModuleType, Any, Any], reranker: FixedReranker, candidates: int
) -> dict[str, Any]:
    tool, _, provisions = corpus
    gs = sys.modules["golden_set"]
    doc = _qrels_doc(corpus)
    rows = tool.questions(ITEMS, doc, frozenset(i["id"] for i in ITEMS))
    hits = [provisions[(gs.CENTRAL, p, n)] for p, n in [(AIR, "1"), (DUP_A, "1"), (WATER, "40")]]
    out = tool.search_all(
        FakeRetrieval(hits),
        FakeSegmenter(),
        rows,
        None,
        qrels=doc["qrels"],
        relevant_from=2,
        k=50,
        on=ON,
        reranker=reranker,
        candidates=candidates,
    )
    return {r.id: r for r in out}


def test_the_rerank_arm_moves_the_head_and_leaves_the_tail_in_place(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    """候選只有前兩條：第 2 條被精排拉到第 1，第 3 條不動，所以 R@50 與沒有精排的臂相同。"""
    got = _rerank_run(corpus, FixedReranker([0.1, 0.9]), candidates=2)

    assert got["second-of-two"].ranking == [
        f"{DUP_A}-art-1",
        f"{AIR}-art-1",
        f"{WATER}-art-40",
    ]
    assert got["second-of-two"].top_rerank == 0.9


def test_the_rerank_arm_reads_the_negatives_scores_for_a_floor(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    got = _rerank_run(corpus, FixedReranker([0.2, 0.7, 0.4]), candidates=3)

    assert got["neg"].ranking == [] and got["neg"].top_rerank == 0.7


def test_a_failed_rerank_aborts_the_measurement_instead_of_measuring_the_first_stage(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    with pytest.raises(RuntimeError, match="endpoint down"):
        _rerank_run(corpus, FixedReranker([], fail=True), candidates=3)


def test_arms_without_a_reranker_record_no_rerank_score(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    out = _measure(corpus, _qrels_doc(corpus), [(AIR, "1")])

    assert all(r.top_rerank is None for r in out)


def test_depth_arms_share_one_scoring_pass_per_question(
    corpus: tuple[ModuleType, Any, Any],
) -> None:
    """打分只呼叫一次（涵蓋最深的深度）；較淺的深度只重排自己的前 N 條，排名與各自單獨呼叫相同。"""
    tool, _, provisions = corpus
    gs = sys.modules["golden_set"]
    doc = _qrels_doc(corpus)
    rows = tool.questions(ITEMS, doc, frozenset(i["id"] for i in ITEMS))
    hits = [provisions[(gs.CENTRAL, p, n)] for p, n in [(AIR, "1"), (DUP_A, "1"), (WATER, "40")]]
    reranker = FixedReranker([0.1, 0.9, 0.5])
    cache: dict[str, Any] = {}

    def arm(depth: int) -> dict[str, Any]:
        out = tool.search_all(
            FakeRetrieval(hits),
            FakeSegmenter(),
            rows,
            None,
            qrels=doc["qrels"],
            relevant_from=2,
            k=50,
            on=ON,
            reranker=reranker,
            candidates=depth,
            rerank_pool=3,
            rerank_cache=cache,
        )
        return {r.id: r for r in out}

    shallow, deep = arm(2), arm(3)

    assert reranker.asked == [3, 3, 3, 3]  # 四題各一次，且都問了最深的 3 條
    assert shallow["second-of-two"].ranking == [
        f"{DUP_A}-art-1",
        f"{AIR}-art-1",
        f"{WATER}-art-40",
    ]
    assert deep["second-of-two"].ranking == [
        f"{DUP_A}-art-1",
        f"{WATER}-art-40",
        f"{AIR}-art-1",
    ]
    assert shallow["neg"].top_rerank == 0.9 and deep["neg"].top_rerank == 0.9
    assert deep["first"].rerank_ms == shallow["first"].rerank_ms is not None  # 共用同一次呼叫
    standalone = _rerank_run(corpus, FixedReranker([0.1, 0.9, 0.5]), candidates=2)
    assert shallow["second-of-two"].ranking == standalone["second-of-two"].ranking
