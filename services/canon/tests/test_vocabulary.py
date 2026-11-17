"""查詢端的詞典隨服務中的分詞器版本更新：不必重啟，換版期間搜尋不中斷。"""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from canon import projection, vocabulary
from canon.db import Conn
from canon.store.canon_store import CanonStore
from canon.vocabulary import EMPTY, Live
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1

ON = "2019-06-01"


def _indexed(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("噪音管制", "乙一")))
    projection.rebuild(conn)


def _new_dictionary(conn: Conn) -> None:
    """詞典多一個法規名稱：分詞器識別碼因此改變，整份索引要重建。"""
    conn.execute("UPDATE corpus.legal_expression SET title = title || '施行細則'")
    conn.commit()
    projection.rebuild(conn)


def test_a_database_without_a_serving_tokenizer_has_an_empty_vocabulary(store: CanonStore) -> None:
    assert vocabulary.serving(store.retrieval) is EMPTY


def test_the_vocabulary_is_the_one_the_index_was_built_with(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _indexed(conn, tmp_path)

    current = vocabulary.serving(store.retrieval)

    assert current.segmenter.tokenizer_id == _scalar(
        conn, "SELECT tokenizer FROM retrieval.serving"
    )
    assert current.law_names.find("依噪音管制法規定") == ["T0000001"]


def test_a_new_dictionary_is_picked_up_without_a_restart(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _indexed(conn, tmp_path)
    live = Live(store.retrieval, interval=0)
    before = live.current().segmenter.tokenizer_id
    assert live.refresh() is False  # 沒有變就不動

    _new_dictionary(conn)

    assert live.refresh() is True
    assert live.current().segmenter.tokenizer_id != before
    assert live.refresh() is False


def test_a_replica_that_has_not_switched_yet_still_finds_everything(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    """藍綠切換的重點：新版本建好、服務切過去之後，還在用舊詞典的副本照常查得到。"""
    _indexed(conn, tmp_path)
    stale = Live(store.retrieval, interval=0).current()

    _new_dictionary(conn)

    hits = store.retrieval.search(
        stale.segmenter.to_tsvector_input("噪音"), tokenizer=stale.segmenter.tokenizer_id, as_of=ON
    )
    assert [h.number for h in hits] == ["1"]


def test_an_algorithm_the_code_cannot_reproduce_is_not_swapped_in(
    conn: Conn, tmp_path: Path, store: CanonStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """索引是別的分詞演算法建的（剛換映像、投影還沒重建）：切不出同樣的詞，維持原樣、等投影。"""
    _indexed(conn, tmp_path)
    live = Live(store.retrieval, interval=0)
    kept = live.current()
    _new_dictionary(conn)

    monkeypatch.setattr("envcite_segmenter.ALGORITHM", "other-algorithm")

    assert live.refresh() is False
    assert live.current() is kept


def test_a_database_error_keeps_the_current_vocabulary() -> None:
    retrieval = MagicMock()
    retrieval.serving_tokenizer.side_effect = [None, RuntimeError("資料庫暫時不可用")]
    live = Live(retrieval, interval=0)

    assert live.refresh() is False
    assert live.current() is EMPTY


def test_the_background_thread_swaps_the_vocabulary(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _indexed(conn, tmp_path)
    live = Live(store.retrieval, interval=0.05)
    before = live.current().segmenter.tokenizer_id
    live.start()
    try:
        _new_dictionary(conn)
        deadline = time.monotonic() + 10
        while live.current().segmenter.tokenizer_id == before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert live.current().segmenter.tokenizer_id != before
    finally:
        live.close()
