"""附表單元對外：它在時點查詢裡，但不出現在條的清單與搜尋結果，也不讓它們出錯。"""

from datetime import date
from pathlib import Path

from canon import projection
from canon.db import Conn
from canon.law import annex
from canon.law.annex import Annex
from canon.store.canon_store import CanonStore
from canon.vocabulary import serving
from tests.test_incremental import _first, _law_id, _law_v1

PCODE = "T0000001"
TEXT = "附表一 噪音管制標準值 日間六十分貝 夜間五十分貝"


def test_an_annex_is_a_unit_at_a_date_but_not_an_article_or_a_search_hit(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _first(conn, tmp_path, _law_v1())
    assert annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True) == "written"
    conn.commit()
    projection.rebuild(conn)

    units = conn.execute(
        "SELECT path, provision_type::text FROM corpus.provisions_at(%s, 'zho', %s)"
        " ORDER BY sort_key",
        (_law_id(conn), date(2020, 1, 1)),
    ).fetchall()
    assert units == [("art_1", "article"), ("art_2", "article"), ("annex_1", "annex")]

    with CanonStore.from_dsn(dsn) as store:
        segmenter = serving(store.retrieval).segmenter

        def hits(query: str) -> set[tuple[str, str]]:
            found = store.retrieval.search(
                segmenter.to_tsvector_input(query),
                tokenizer=segmenter.tokenizer_id,
                as_of="2020-01-01",
            )
            return {(h.local_id, h.number) for h in found}

        assert hits("夜間五十分貝") == set()  # 附表的文字不會讓搜尋出錯，也不當成條回出去
        assert ("T0000001", "1") in hits("甲一")  # 條照常
