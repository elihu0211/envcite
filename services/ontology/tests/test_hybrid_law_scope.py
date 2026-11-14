"""0047：混合檢索可限定在指定的法規。

不限定時與 0045 逐位元相同（0045 的定義以另一個名字建出來當對照）；限定時兩臂的候選都只在
範圍內，語意臂不因為 HNSW 先取近鄰再過濾而落空。
"""

from pathlib import Path

import psycopg

from envcite_ontology import projection
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_ontology.store.retrieval import SEARCH_POOL
from envcite_segmenter import Segmenter
from tests.sample import _law, stored_snapshot
from tests.test_hybrid import FakeEmbedder

Conn = psycopg.Connection[tuple[object, ...]]

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ON = "2019-06-01"
HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
#: 三部法規，主題各異；每部都有「檢查」，只有一部有各自的主題詞
LAWS = (
    ("T0000001", "廢水管理法", "排放廢水者應符合放流水標準", "主管機關得派員檢查"),
    ("T0000002", "噪音管制法", "管制區內不得製造噪音", "主管機關得派員檢查"),
    ("T0000003", "地震防救法", "地震後應檢查建物", "主管機關得派員檢查"),
)


def _previous(conn: Conn) -> None:
    sql = (MIGRATIONS / "0045_hybrid_query_once.sql").read_text(encoding="utf-8")
    conn.execute(sql.replace("retrieval.search_hybrid(", "retrieval.search_hybrid_prev("))


def _corpus(conn: Conn, tmp_path: Path) -> FakeEmbedder:
    laws = [
        _law(pcode, title, HISTORY, "20180613", [("1", first), ("2", second)])
        for pcode, title, first, second in LAWS
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    return embedder


def _resource(conn: Conn, pcode: str) -> int:
    row = conn.execute(
        "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
        (pcode,),
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def test_without_a_scope_nothing_changes(conn: Conn, tmp_path: Path, dsn: str) -> None:
    embedder = _corpus(conn, tmp_path)
    _previous(conn)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        for query in ("廢水 檢查", "噪音", "地震 建物", "檢查"):
            terms = segmenter.to_tsvector_input(query)
            for vector, model in ((None, ""), (embedder.embed([query])[0], embedder.model)):
                params = {
                    "terms": terms,
                    "tokenizer": segmenter.tokenizer_id,
                    "embedding": None if vector is None else str(list(vector)),
                    "model": model,
                    "on": ON,
                    "limit": 50,
                    "pool": SEARCH_POOL,
                }
                now = conn.execute(
                    "SELECT * FROM retrieval.search_hybrid(%(terms)s, %(tokenizer)s,"
                    " %(embedding)s::vector, %(model)s, %(on)s::date, 'zho', %(limit)s,"
                    " %(pool)s, NULL)",
                    params,
                ).fetchall()
                before = conn.execute(
                    "SELECT * FROM retrieval.search_hybrid_prev(%(terms)s, %(tokenizer)s,"
                    " %(embedding)s::vector, %(model)s, %(on)s::date, 'zho', %(limit)s,"
                    " %(pool)s)",
                    params,
                ).fetchall()
                assert now, query
                assert now == before, query


def test_both_arms_stay_inside_the_scope(conn: Conn, tmp_path: Path, dsn: str) -> None:
    embedder = _corpus(conn, tmp_path)
    scope = [_resource(conn, "T0000002")]
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        # 「檢查」三部法規都有；「廢水」只在另一部：字面臂在範圍內找不到它
        for query in ("檢查", "廢水"):
            vector = embedder.embed([query])[0]
            rows = conn.execute(
                "SELECT resource_id, lexical_score, semantic_similarity"
                "  FROM retrieval.search_hybrid("
                "%s, %s, %s::vector, %s, %s::date, 'zho', 50, %s, %s)",
                (
                    segmenter.to_tsvector_input(query),
                    segmenter.tokenizer_id,
                    str(list(vector)),
                    embedder.model,
                    ON,
                    SEARCH_POOL,
                    scope,
                ),
            ).fetchall()
            assert rows, query
            assert {r[0] for r in rows} == set(scope), query
            # 語意臂在範圍內是精確掃描：範圍內每一條都有相似度，不論多遠
            assert all(r[2] is not None for r in rows), query
        assert len(rows) == 2


def test_the_search_follows_the_named_laws_and_falls_back_to_everything(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        tokenizer = segmenter.tokenizer_id
        terms = segmenter.to_tsvector_input("檢查")
        assert {
            h.local_id for h in store.retrieval.search(terms, tokenizer=tokenizer, as_of=ON)
        } == {p for p, *_ in LAWS}
        scoped = store.retrieval.search(terms, tokenizer=tokenizer, as_of=ON, laws=["T0000003"])
        assert {h.local_id for h in scoped} == {"T0000003"}
        # 點名的法規裡沒有任何命中（字面臂找不到、沒有向量）：改在全庫找，不回空
        elsewhere = segmenter.to_tsvector_input("廢水")
        hits = store.retrieval.search(elsewhere, tokenizer=tokenizer, as_of=ON, laws=["T0000002"])
        assert {h.local_id for h in hits} == {"T0000001"}
