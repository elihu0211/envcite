"""0045 只改查詢的算法（tsquery 只算一次），不改結果：與 0039 的定義逐位元相同。

0039 的函式在測試裡以另一個名字建出來當對照；兩者對同一批查詢回的列（版本、名次分數、
字面分數、語意相似度，含順序）必須完全相等。
"""

from pathlib import Path

import psycopg

from envcite_ontology import projection
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store import SEARCH_POOL, OntologyStore
from envcite_segmenter import Segmenter
from tests.sample import _law, stored_snapshot
from tests.test_hybrid import FakeEmbedder

Conn = psycopg.Connection[tuple[object, ...]]

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
ON = "2019-06-01"
HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
THEMES = ("廢水", "噪音", "地震", "建物", "空氣", "排放", "檢查", "罰鍰")


def _previous(conn: Conn) -> None:
    """把 0039 的 `search_hybrid` 以 `search_hybrid_prev` 建出來（交易內，測完回滾）。"""
    sql = (MIGRATIONS / "0039_hybrid_tiebreak.sql").read_text(encoding="utf-8")
    assert "retrieval.search_hybrid(" in sql
    conn.execute(sql.replace("retrieval.search_hybrid(", "retrieval.search_hybrid_prev("))


def test_the_query_is_built_once_and_nothing_else_changes(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    laws = [
        _law(
            f"T000000{i}",
            f"法{i}",
            HISTORY,
            "20180613",
            [
                (str(n), f"{THEMES[(n + i) % 8]}與{THEMES[(n * 3 + i) % 8]}之管理，應依本法辦理。")
                for n in range(1, 9)
            ],
        )
        for i in (1, 2, 3)
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    _previous(conn)

    body = conn.execute(
        "SELECT prosrc FROM pg_proc WHERE proname = 'search_hybrid'"
        " AND pronamespace = 'retrieval'::regnamespace"
    ).fetchone()
    assert body is not None
    assert "AS MATERIALIZED" in str(body[0])

    queries = [f"{a} {b}" for a in THEMES for b in THEMES if a != b][:30]
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.lexicon())
        for query in queries:
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
                rows = {}
                for name in ("search_hybrid", "search_hybrid_prev"):
                    rows[name] = conn.execute(
                        f"SELECT * FROM retrieval.{name}(%(terms)s, %(tokenizer)s,"
                        " %(embedding)s::vector, %(model)s, %(on)s::date, 'zho',"
                        " %(limit)s, %(pool)s)",
                        params,
                    ).fetchall()
                assert rows["search_hybrid"], query
                assert rows["search_hybrid"] == rows["search_hybrid_prev"], query
