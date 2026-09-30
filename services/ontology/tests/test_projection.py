from pathlib import Path

import psycopg

from env_law_ontology import projection
from env_law_ontology.acquire.moj import Moj
from env_law_ontology.ingest import importer
from env_law_ontology.segmentation import ActiveSegmenter
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter
from tests.test_import import _law, _snapshot

Conn = psycopg.Connection[tuple[object, ...]]


def _imported(conn: Conn, tmp_path: Path, *laws: dict[str, object]) -> None:
    _snapshot(conn, tmp_path, list(laws))
    importer.run(conn, tmp_path, Moj())


def _tokenizers(conn: Conn) -> dict[str, bool]:
    return {
        str(t): bool(a)
        for t, a in conn.execute("SELECT tokenizer, active FROM retrieval.projection")
    }


def _index_tokenizers(conn: Conn) -> set[str]:
    return {
        str(t) for (t,) in conn.execute("SELECT DISTINCT tokenizer FROM retrieval.article_index")
    }


def test_imported_articles_become_searchable(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    assert projection.rebuild(conn) >= 2

    with OntologyStore.from_dsn(dsn) as store:
        segmenter = ActiveSegmenter(store).current()

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
    first = projection.rebuild(conn)
    assert first >= 2 and projection.rebuild(conn) == 0
    assert list(_tokenizers(conn).values()) == [True]


def test_a_new_title_switches_tokenizer_without_a_gap_and_recycles_a_cycle_later(
    conn: Conn, tmp_path: Path
) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    projection.rebuild(conn)
    (old,) = _tokenizers(conn)

    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"), _law("T0000002", "振動管制法"))
    projection.rebuild(conn)
    state = _tokenizers(conn)
    (new,) = (t for t, active in state.items() if active)
    assert new != old
    # 翻轉之後舊識別碼的列仍在：尚未切換的服務還有列可查。
    assert set(state) == {old, new} and _index_tokenizers(conn) == {old, new}

    projection.rebuild(conn)
    assert _tokenizers(conn) == {new: True} and _index_tokenizers(conn) == {new}


def test_the_server_follows_the_active_tokenizer_without_a_restart(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _imported(conn, tmp_path, _law("T0000001", "噪音管制法"))
    projection.rebuild(conn)
    with OntologyStore.from_dsn(dsn) as store:
        active = ActiveSegmenter(store)
        first = active.current().tokenizer_id
        assert active.current().tokenizer_id == first

        _imported(conn, tmp_path, _law("T0000001", "噪音管制法"), _law("T0000002", "振動管制法"))
        projection.rebuild(conn)
        second = active.current().tokenizer_id
    assert second != first
    assert "振動管制法" in Segmenter(["振動管制法"]).segment("振動管制法")


def test_index_rows_of_superseded_versions_are_recycled(conn: Conn, tmp_path: Path) -> None:
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
