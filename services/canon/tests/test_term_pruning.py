"""字面臂略過高頻詞：詞頻表隨投影重算，查詢詞依比例修剪，且永遠不會變成空查詢。"""

from pathlib import Path

import pytest

from canon import projection
from canon.db import Conn
from canon.store.canon_store import CanonStore
from canon.vocabulary import serving
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1

ON = "2019-06-01"
#: 「common」在每一段都有；其餘各只出現一段。拉丁字母詞不被切成二字詞，詞頻好算。
TEXTS = ("common alpha", "common beta", "common gamma", "common delta", "common epsilon")


def _corpus(conn: Conn, tmp_path: Path) -> str:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(*TEXTS)))
    projection.rebuild(conn)
    return str(_scalar(conn, "SELECT tokenizer FROM retrieval.serving"))


def _prune(conn: Conn, terms: str, tokenizer: str, max_df: float | None) -> str:
    return str(
        _scalar(
            conn,
            "SELECT retrieval.prune_terms(%s, %s, %s::double precision)",
            terms,
            tokenizer,
            max_df,
        )
    )


def test_projection_records_document_frequency(conn: Conn, tmp_path: Path) -> None:
    tokenizer = _corpus(conn, tmp_path)

    freq = {
        str(term): freq
        for term, freq in conn.execute(
            "SELECT term, doc_freq FROM retrieval.term_stat WHERE tokenizer = %s", (tokenizer,)
        )
    }
    assert freq["common"] == 5
    assert freq["beta"] == 1
    assert _scalar(conn, "SELECT passages FROM retrieval.term_stat_total") == 5


def test_a_new_tokenizer_gets_its_own_statistics_and_the_old_ones_go_after_the_grace(
    conn: Conn, tmp_path: Path
) -> None:
    old = _corpus(conn, tmp_path)
    conn.execute(
        "UPDATE corpus.legal_expression SET title = title || '施行細則' WHERE title IS NOT NULL"
    )
    conn.commit()

    projection.rebuild(conn)

    new = str(_scalar(conn, "SELECT tokenizer FROM retrieval.serving"))
    assert new != old
    # 寬限期內舊版本的詞頻還在：還沒換過來的副本照常修剪
    assert _prune(conn, "common beta", old, 0.2) == "beta"
    assert _prune(conn, "common beta", new, 0.2) == "beta"
    conn.execute(
        "UPDATE retrieval.tokenizer SET retired_at = now() - interval '1 hour' WHERE id = %s",
        (old,),
    )
    conn.commit()

    projection.rebuild(conn)

    assert {t for (t,) in conn.execute("SELECT tokenizer FROM retrieval.term_stat")} == {new}
    assert _scalar(conn, "SELECT count(*) FROM retrieval.term_stat_total") == 1


def test_terms_above_the_ratio_are_dropped_in_order(conn: Conn, tmp_path: Path) -> None:
    tokenizer = _corpus(conn, tmp_path)

    assert _prune(conn, "common beta gamma", tokenizer, 0.5) == "beta gamma"
    assert _prune(conn, "beta common gamma", tokenizer, 0.5) == "beta gamma"
    # 比例是「含該詞的段落數除以總數」：beta 是 1/5，剛好在 0.2 上限內
    assert _prune(conn, "common beta", tokenizer, 0.2) == "beta"
    assert _prune(conn, "common beta", tokenizer, 0.1) == "beta"  # 都超過時留最稀有的


def test_all_common_terms_never_becomes_an_empty_query(conn: Conn, tmp_path: Path) -> None:
    tokenizer = _corpus(conn, tmp_path)
    conn.execute(
        "UPDATE retrieval.term_stat SET doc_freq = 4 WHERE term = 'beta' AND tokenizer = %s",
        (tokenizer,),
    )
    conn.commit()

    assert _prune(conn, "common", tokenizer, 0.01) == "common"
    # 全部超標：留最稀有的那個，同稀有度取先出現的
    assert _prune(conn, "common beta", tokenizer, 0.01) == "beta"
    assert _prune(conn, "gamma common", tokenizer, 0.0001) == "gamma"


def test_terms_without_statistics_are_kept(conn: Conn, tmp_path: Path) -> None:
    tokenizer = _corpus(conn, tmp_path)

    assert _prune(conn, "common 未知詞", tokenizer, 0.5) == "未知詞"
    assert _prune(conn, "未知詞 另一個", tokenizer, 0.5) == "未知詞 另一個"


@pytest.mark.parametrize("max_df", [None, 0.0, -1.0])
def test_a_disabled_ratio_returns_the_terms_untouched(
    conn: Conn, tmp_path: Path, max_df: float | None
) -> None:
    tokenizer = _corpus(conn, tmp_path)

    assert _prune(conn, "common beta", tokenizer, max_df) == "common beta"


def test_without_statistics_for_the_tokenizer_nothing_is_pruned(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path)

    assert _prune(conn, "common beta", "other-tokenizer", 0.01) == "common beta"


def test_search_finds_the_same_article_with_and_without_pruning(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)

    with CanonStore.from_dsn(dsn) as store:
        segmenter = serving(store.retrieval).segmenter
        terms = segmenter.to_tsvector_input("common beta")
        plain = store.retrieval.search(terms, tokenizer=segmenter.tokenizer_id, as_of=ON, limit=5)
        pruned = store.retrieval.search(
            terms, tokenizer=segmenter.tokenizer_id, as_of=ON, limit=5, max_df=0.5
        )

    # 不修剪時 common 讓五條都命中；修剪後只剩 beta 命中的那一條，召回的目標仍是第一名
    assert len(plain) == 5
    assert [h.number for h in pruned] == [plain[0].number]


def test_search_with_only_common_terms_still_returns_hits(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)

    with CanonStore.from_dsn(dsn) as store:
        segmenter = serving(store.retrieval).segmenter
        hits = store.retrieval.search(
            segmenter.to_tsvector_input("common"),
            tokenizer=segmenter.tokenizer_id,
            as_of=ON,
            limit=5,
            max_df=0.01,
        )

    assert len(hits) == 5
