"""函釋提到查詢詞時帶出它解釋的條文：只拿函釋找條文，錨定、發文日與停止適用的判斷與附在推論旁的相同。"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import grpc
import pytest

from canon import violation
from canon.db import Conn, fetch
from canon.guidance_search import EXTRA, fuse, guidance_phrases
from canon.law.guidance import load
from canon.server import build_server
from canon.store.canon_store import CanonStore
from canon.store.records import SearchHit
from envcite.canon.v1 import canon_pb2, canon_pb2_grpc
from tests.conftest import call_metadata
from tests.test_annotation import LAW
from tests.test_guidance import _doc, _letter
from tests.test_incremental import _articles as law_articles
from tests.test_incremental import _first, _law_v1
from tests.test_reasoning_transition import _amended


class _Article(NamedTuple):
    number: str
    documents: int


ON = "2019-06-01"
PHRASE = "露天燃燒"


def _burning(no: str, issued_on: str, **over: Any) -> dict[str, Any]:
    """主旨提到查詢詞、解釋第 1 條（條文原文是「甲一」，沒有這個詞）。"""
    return _letter(no, issued_on, subject=f"有關{PHRASE}之認定", **over)


def _articles(conn: Conn, phrases: list[str], on: str = ON, max_df: float = 0.0) -> list[_Article]:
    return fetch(
        conn,
        "SELECT corpus.article_number(g.path), g.documents"
        "  FROM retrieval.guidance_articles(%s, %s, 'zho', 10, %s) AS g ORDER BY 1",
        (phrases, on, max_df),
        _Article,
    )


def test_a_letter_naming_the_phrase_brings_out_the_article_it_interprets(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_burning("甲字第1號", "2019-03-01"), _burning("甲字第2號", "2019-04-01")))

    assert _articles(conn, [PHRASE]) == [("1", 2)]  # 兩件函釋提到、都解釋第 1 條
    assert _articles(conn, ["不相干的詞"]) == []
    assert _articles(conn, []) == []


def test_an_interpreted_duty_brings_its_sanction_chain_with_no_letter_count(
    conn: Conn, tmp_path: Path
) -> None:
    """函釋解釋義務條：處罰它的條與處罰的處罰跟著出來，與混合檢索命中義務條時同一份展開。
    處罰條沒有函釋提到它，件數為 0；次序緊跟在來源之後。"""
    _first(
        conn,
        tmp_path,
        _law_v1(
            LawArticles=law_articles(
                "甲一",
                "違反第一條規定者，處新臺幣一萬元以上罰鍰。\r\n依前項處罰者，得令其停止作為。",
                "不遵行主管機關依第二條第二項所為停止作為之命令者，處一年以下有期徒刑。",
                "無關的條文",
            )
        ),
    )
    violation.extract(conn)
    load(conn, _doc(_burning("甲字第1號", "2019-03-01")))
    conn.commit()

    rows = fetch(
        conn,
        "SELECT corpus.article_number(g.path), g.documents"
        "  FROM retrieval.guidance_articles(%s, %s, 'zho', 10, 0) AS g",
        ([PHRASE], ON),
        _Article,
    )

    assert rows == [("1", 1), ("2", 0), ("3", 0)]  # 義務、罰鍰、不遵行命令的刑責；第 4 條不相干


def test_an_article_that_is_both_interpreted_and_a_sanction_keeps_its_letter_count(
    conn: Conn, tmp_path: Path
) -> None:
    _first(
        conn,
        tmp_path,
        _law_v1(LawArticles=law_articles("甲一", "違反第一條規定者，處新臺幣一萬元以上罰鍰。")),
    )
    violation.extract(conn)
    # 兩件函釋各解釋第 1 條與第 2 條（第 2 條的原文是處罰句，同時是第 1 條的處罰條）
    load(
        conn,
        _doc(
            _burning("甲字第1號", "2019-03-01"),
            _burning(
                "甲字第2號",
                "2019-04-01",
                interprets=[
                    {
                        "law": LAW,
                        "path": "art_2",
                        "text": "違反第一條規定者，處新臺幣一萬元以上罰鍰。",
                    }
                ],
            ),
        ),
    )
    conn.commit()

    rows = fetch(
        conn,
        "SELECT corpus.article_number(g.path), g.documents"
        "  FROM retrieval.guidance_articles(%s, %s, 'zho', 10, 0) AS g",
        ([PHRASE], ON),
        _Article,
    )

    assert sorted(rows) == [
        ("1", 1),
        ("2", 1),
    ]  # 第 2 條是處罰條、也被函釋直接解釋：件數不被展開蓋成 0


def test_the_repository_keeps_each_sanction_right_after_the_article_that_brought_it(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    """展開出來的處罰條（件數 0）緊跟在它的來源之後，不被依件數重排到所有來源後面：名次太後，
    融合時會被截掉。"""
    _first(
        conn,
        tmp_path,
        _law_v1(
            LawArticles=law_articles("甲一", "違反第一條規定者，處新臺幣一萬元以上罰鍰。", "丙三")
        ),
    )
    violation.extract(conn)
    third = {"law": LAW, "path": "art_3", "text": "丙三"}
    load(
        conn,
        _doc(
            _burning("甲字第1號", "2019-03-01"),
            _burning("甲字第2號", "2019-04-01"),
            _burning("甲字第3號", "2019-05-01", interprets=[third]),
        ),
    )
    conn.commit()

    with CanonStore.from_dsn(dsn) as store:
        hits = store.retrieval.guidance_hits([PHRASE], as_of=ON, limit=10)

    # 第 1 條（兩件函釋）→ 它的罰則第 2 條（0 件）→ 第 3 條（一件函釋）
    assert [(h.number, documents) for h, documents in hits] == [("1", 2), ("2", 0), ("3", 1)]


def test_every_phrase_must_appear_in_the_letter(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_burning("甲字第1號", "2019-03-01")))

    assert _articles(conn, [PHRASE, "增補"]) == []
    assert _articles(conn, [PHRASE, "第一條所稱排放"]) == [("1", 1)]  # 內文也算


def test_a_letter_counts_only_while_it_is_in_effect(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_burning("甲字第1號", "2019-03-01", ceased_on="2019-05-01")))

    assert _articles(conn, [PHRASE], "2019-02-01") == []  # 還沒發文
    assert _articles(conn, [PHRASE], "2019-04-01") == [("1", 1)]
    assert _articles(conn, [PHRASE], "2019-05-01") == []  # 已停止適用


def test_an_amended_text_no_longer_brings_the_article_out(conn: Conn, tmp_path: Path) -> None:
    """解釋錨定的是那段文字：第二條修正（乙一 → 乙二）後，舊文字的函釋帶不出新條文。"""
    _amended(conn, tmp_path)
    interprets = [{"law": LAW, "path": "art_2", "text": "乙一"}]
    load(conn, _doc(_burning("甲字第1號", "2019-03-01", interprets=interprets)))

    assert _articles(conn, [PHRASE], "2019-06-01") == [("2", 1)]
    assert _articles(conn, [PHRASE], "2020-06-01") == []


def test_a_phrase_in_too_many_letters_is_not_an_entrance(conn: Conn, tmp_path: Path) -> None:
    """命中的函釋占可用函釋的比例超過上限，就指不出哪幾條：整個不用，不挑其中幾件。"""
    _first(conn, tmp_path, _law_v1())
    # 四件可用函釋，兩件提到「露天燃燒」（占 50%）、四件都提到「說明」。
    load(
        conn,
        _doc(
            _burning("甲字第1號", "2019-03-01"),
            _burning("甲字第2號", "2019-03-02"),
            _letter("甲字第3號", "2019-03-03", subject="另一件的主旨"),
            _letter("甲字第4號", "2019-03-04", subject="再一件的主旨"),
        ),
    )

    assert _articles(conn, [PHRASE], max_df=0.5) == [("1", 2)]  # 剛好在上限：照常
    assert _articles(conn, [PHRASE], max_df=0.49) == []  # 超過上限：不用
    assert _articles(conn, ["說明"], max_df=0.99) == []  # 四件都提到：太常見
    assert _articles(conn, ["說明"], max_df=1.0) == [("1", 4)]
    assert _articles(conn, [PHRASE], max_df=0.0) == [("1", 2)]  # 0 為不限制


def test_the_ratio_counts_only_letters_in_effect(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(
        conn,
        _doc(
            _burning("甲字第1號", "2019-03-01"),
            _letter("甲字第2號", "2019-03-02", subject="停止適用", ceased_on="2019-04-01"),
        ),
    )

    # 5 月只剩一件可用函釋（停止適用的那件不在分母裡），它提到詞組：占 100%，超過 0.99 就不用。
    assert _articles(conn, [PHRASE], "2019-05-01", max_df=0.99) == []
    assert _articles(conn, [PHRASE], "2019-05-01", max_df=1.0) == [("1", 1)]
    # 3 月兩件都可用：一件提到，占 50%。
    assert _articles(conn, [PHRASE], "2019-03-15", max_df=0.5) == [("1", 1)]
    assert _articles(conn, [PHRASE], "2019-03-15", max_df=0.49) == []


@pytest.fixture
def search_stub(
    conn: Conn, store: CanonStore, tmp_path: Path
) -> Iterator[canon_pb2_grpc.CanonServiceStub]:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_burning("甲字第1號", "2019-03-01")))
    conn.commit()
    server, port = build_server(store, "127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield canon_pb2_grpc.CanonServiceStub(channel)
    server.stop(grace=None)


def _search(
    stub: canon_pb2_grpc.CanonServiceStub, query: str, *, guidance: bool
) -> canon_pb2.SearchArticlesResponse:
    return stub.SearchArticles(
        canon_pb2.SearchArticlesRequest(query=query, as_of=ON, include_guidance=guidance),
        metadata=call_metadata(),
    )


def test_the_service_returns_guidance_articles_only_when_asked(
    search_stub: canon_pb2_grpc.CanonServiceStub,
) -> None:
    assert list(_search(search_stub, PHRASE, guidance=False).hits) == []

    (hit,) = _search(search_stub, PHRASE, guidance=True).hits
    assert (hit.law_title, hit.number, hit.guidance_documents) == ("噪音管制法", "1", 1)
    assert hit.lexical_score == 0  # 不是字面臂找到的
    assert not hit.HasField("semantic_similarity")


def test_a_query_no_letter_names_brings_out_nothing(
    search_stub: canon_pb2_grpc.CanonServiceStub,
) -> None:
    assert list(_search(search_stub, "完全沒人提過的詞", guidance=True).hits) == []


def _hit(n: int) -> SearchHit:
    return SearchHit(
        iri=f"iri-{n}",
        jurisdiction="TW-CENTRAL",
        id_scheme="moj_pcode",
        local_id="T0000001",
        law_title="法",
        number=str(n),
        content="",
        lexical_score=0.0,
        semantic_similarity=None,
    )


def test_phrases_are_the_whitespace_separated_words_of_a_sensible_length() -> None:
    assert guidance_phrases("露天燃燒  稻草 露天燃燒 a") == ["露天燃燒", "稻草"]
    assert guidance_phrases("甲 乙") == []
    assert len(guidance_phrases(" ".join(f"詞組{n}" for n in range(9)))) == 5
    assert guidance_phrases("字" * 31) == []


def test_guidance_articles_join_the_ranking_without_displacing_the_arms() -> None:
    hits = [_hit(n) for n in range(1, 11)]
    guided = [(_hit(n), 3) for n in (20, 21, 22, 23, 24, 25, 26)]

    fused = fuse(hits, guided, limit=10)

    assert len(fused) == 10 + EXTRA
    assert [h.iri for h, _ in fused][:2] == ["iri-1", "iri-20"]  # 同名次兩臂在前
    assert all(documents == 0 for h, documents in fused if h.iri in {x.iri for x in hits})


def test_a_sanction_shares_the_rank_of_its_source_and_pushes_no_other_source_down() -> None:
    """展開出來的處罰條（件數 0）與來源同名次：後面的來源名次不變，與兩臂結果的相對次序不變。"""
    hits = [_hit(n) for n in range(1, 11)]
    sources = [(_hit(20), 3), (_hit(21), 2), (_hit(22), 1)]
    expanded = [sources[0], (_hit(90), 0), (_hit(91), 0), *sources[1:]]

    plain = [h.iri for h, _ in fuse(hits, sources, limit=10)]
    chained = [h.iri for h, _ in fuse(hits, expanded, limit=10)]

    assert chained.index("iri-90") == chained.index("iri-20") + 1  # 緊跟在來源之後
    assert chained.index("iri-91") == chained.index("iri-90") + 1
    kept = [iri for iri in chained if iri in plain]
    assert kept == [iri for iri in plain if iri in kept]  # 原有的次序一個都沒變


def test_an_article_in_both_ranks_above_one_in_either() -> None:
    fused = fuse([_hit(1), _hit(2)], [(_hit(2), 4)], limit=10)

    assert [(h.iri, documents) for h, documents in fused] == [("iri-2", 4), ("iri-1", 0)]
