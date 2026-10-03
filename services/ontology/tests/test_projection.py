from pathlib import Path

import psycopg

from env_law_ontology import projection
from env_law_ontology.acquire.moj import Moj
from env_law_ontology.ingest import importer
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter
from tests.sample import stored_snapshot
from tests.test_import import _law, _scalar
from tests.test_incremental import _articles, _first, _law_v1, _next

Conn = psycopg.Connection[tuple[object, ...]]


def _imported(conn: Conn, tmp_path: Path, *laws: dict[str, object]) -> None:
    stored_snapshot(conn, tmp_path, list(laws))
    importer.run(conn, tmp_path, Moj())


def _tokenizers(conn: Conn) -> set[str]:
    return {
        str(t) for (t,) in conn.execute("SELECT DISTINCT tokenizer FROM retrieval.lexical_index")
    }


def _found(dsn: str, query: str) -> set[tuple[str, str]]:
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.lexicon())
        hits = store.search(
            segmenter.to_tsvector_input(query),
            tokenizer=segmenter.tokenizer_id,
            as_of="2020-01-01",
        )
        return {(h.law_id, h.number) for h in hits}


def test_imported_articles_become_searchable(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    assert projection.rebuild(conn) > 1

    assert ("T0000001", "1") in _found(dsn, "為防制噪音")
    # 第 2 條的「振動」只在款裡：索引的單位是段落，命中依路徑歸到條。
    assert ("T0000001", "2") in _found(dsn, "振動")


def test_rebuild_builds_only_what_is_missing(conn: Conn, tmp_path: Path) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    assert projection.rebuild(conn) > 1
    assert projection.rebuild(conn) == 0


def test_a_new_title_changes_the_tokenizer_and_rebuilds_everything(
    conn: Conn, tmp_path: Path
) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    first = projection.rebuild(conn)
    (old,) = _tokenizers(conn)

    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"), _law("T0000002", "振動管制法"))
    # 全部重建；兩部法規的段落文字一字不差，內容定址使它們共用同一批索引列，所以列數不增。
    assert projection.rebuild(conn) == first
    (new,) = _tokenizers(conn)
    assert new != old


def test_text_no_longer_current_loses_its_index_row(conn: Conn, tmp_path: Path) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    projection.rebuild(conn)
    content = str(
        _scalar(
            conn, "SELECT content FROM corpus.provision_version WHERE content LIKE '為防制噪音%'"
        )
    )
    conn.execute(
        "UPDATE corpus.provision_version SET superseded_at = now() + interval '1 second'"
        " WHERE content = %s",
        (content,),
    )
    conn.commit()
    projection.rebuild(conn)
    row = conn.execute(
        "SELECT count(*) FROM retrieval.lexical_index"
        " WHERE content_sha256 = sha256(convert_to(%s, 'UTF8'))",
        (content,),
    ).fetchone()
    assert row == (0,)


def test_a_correction_inside_an_article_is_searched_after_the_rebuild(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    """F2：條下單元更正後，搜尋比對新文字、不比對舊文字。索引逐段落、以文字雜湊為鍵，不論條的列有沒有換。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("噪音管制\n振動標準", "乙一")))
    projection.rebuild(conn)
    assert ("T0000001", "1") in _found(dsn, "標準")

    corrected = _law_v1(LawArticles=_articles("噪音管制\n振動基準", "乙一"))
    assert _next(conn, tmp_path, corrected).actions == {"corrected": 1}
    projection.rebuild(conn)

    assert ("T0000001", "1") in _found(dsn, "基準")
    assert ("T0000001", "1") not in _found(dsn, "標準")


def test_the_same_text_in_a_new_row_needs_no_rebuild(conn: Conn, tmp_path: Path) -> None:
    """效力列更替而文字沒變：新列帶著同一個雜湊，原有的索引列沿用。"""
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    projection.rebuild(conn)
    conn.execute(
        "UPDATE corpus.provision_version SET superseded_at = now() + interval '1 second'"
        " WHERE content LIKE '為防制噪音%'"
    )
    conn.execute(
        "INSERT INTO corpus.provision_version"
        " (provision_id, expression_id, resource_id, content, force_period, commence_rule)"
        " SELECT provision_id, expression_id, resource_id, content, force_period, commence_rule"
        "   FROM corpus.provision_version WHERE content LIKE '為防制噪音%'"
    )
    conn.commit()
    assert projection.rebuild(conn) == 0


def test_identical_passages_share_one_index_row(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("同文", "同文")))
    projection.rebuild(conn)
    assert _scalar(conn, "SELECT count(*) FROM retrieval.lexical_index") == 1
