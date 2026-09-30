from pathlib import Path

import psycopg

from env_law_ontology import projection
from env_law_ontology.acquire.moj import Moj
from env_law_ontology.ingest import importer
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter
from tests.test_import import _law, _snapshot

Conn = psycopg.Connection[tuple[object, ...]]


def _imported(conn: Conn, tmp_path: Path, *laws: dict[str, object]) -> None:
    _snapshot(conn, tmp_path, list(laws))
    importer.run(conn, tmp_path, Moj())


def _segmenter(conn: Conn) -> Segmenter:
    titles = [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    return Segmenter(titles)


def test_imported_articles_become_searchable(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    assert projection.rebuild(conn) >= 2

    segmenter = _segmenter(conn)

    def found(query: str) -> set[tuple[str, str]]:
        with OntologyStore.from_dsn(dsn) as store:
            hits = store.search(
                segmenter.to_tsvector_input(query),
                tokenizer=segmenter.tokenizer_id,
                as_of="2020-01-01",
            )
        return {(h.law_id, h.number) for h in hits}

    assert ("T0000001", "1") in found("為防制噪音")
    # 第 2 條的「振動」只在款裡：條的全文由下層單元組成。
    assert ("T0000001", "2") in found("振動")


def test_rebuild_is_incremental_and_replaces_rows_of_another_tokenizer(
    conn: Conn, tmp_path: Path
) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    first = projection.rebuild(conn)
    assert projection.rebuild(conn) == 0

    # 新增一部法規：標題進入詞典，識別碼改變，先前的列全數作廢重建。
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"), _law("T0000002", "振動管制法"))
    assert projection.rebuild(conn) >= first
    assert conn.execute(
        "SELECT count(DISTINCT tokenizer) FROM retrieval.article_index"
    ).fetchone() == (1,)
