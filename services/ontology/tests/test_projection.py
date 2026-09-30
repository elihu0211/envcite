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


def _tokenizers(conn: Conn) -> set[str]:
    return {
        str(t) for (t,) in conn.execute("SELECT DISTINCT tokenizer FROM retrieval.article_index")
    }


def test_imported_articles_become_searchable(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    assert projection.rebuild(conn) > 1

    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.lexicon())

        def found(query: str) -> set[tuple[str, str]]:
            hits = store.search(
                segmenter.to_tsvector_input(query),
                tokenizer=segmenter.tokenizer_id,
                as_of="2020-01-01",
            )
            return {(h.law_id, h.number) for h in hits}

        assert ("T0000001", "1") in found("為防制噪音")
        # 第 2 條的「振動」只在款裡：條的全文由下層單元組成。
        assert ("T0000001", "2") in found("振動")


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
    assert projection.rebuild(conn) > first
    (new,) = _tokenizers(conn)
    assert new != old


def test_index_rows_of_superseded_versions_are_dropped(conn: Conn, tmp_path: Path) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    projection.rebuild(conn)
    conn.execute(
        "UPDATE corpus.provision_version SET superseded_at = now() + interval '1 second'"
        " WHERE content LIKE '為防制噪音%'"
    )
    conn.commit()
    projection.rebuild(conn)
    row = conn.execute(
        "SELECT count(*) FROM retrieval.article_index AS i"
        " JOIN corpus.provision_version AS v ON v.id = i.provision_version_id"
        " WHERE v.superseded_at IS NOT NULL"
    ).fetchone()
    assert row == (0,)
