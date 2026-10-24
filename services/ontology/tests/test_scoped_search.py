"""`tools/scoped_search.py` 重現 `retrieval.search_hybrid`：不加範圍時與服務端的結果完全相同，
加範圍時兩臂的候選池在取候選之前就只含範圍內的法規。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import psycopg

from envcite_ontology import projection
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter
from tests.sample import _law, stored_snapshot
from tests.test_hybrid import FakeEmbedder

Conn = psycopg.Connection[tuple[object, ...]]

TOOL = Path(__file__).resolve().parents[1] / "tools" / "scoped_search.py"
ON = "2019-06-01"
HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
THEMES = ("廢水", "噪音", "地震", "建物", "空氣", "排放", "檢查", "罰鍰")
# 兩部法規，條文用相同的主題詞，使同一個查詢在兩部之間競爭候選
LAWS = {"T0000001": "甲污染法", "T0000002": "乙管制法"}


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scoped_search", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["scoped_search"] = module
    spec.loader.exec_module(module)
    return module


def _import(conn: Conn, tmp_path: Path) -> None:
    laws = [
        _law(
            pcode,
            name,
            HISTORY,
            "20180613",
            [
                (str(n), f"{THEMES[(n + i) % 8]}與{THEMES[(n * 3 + i) % 8]}之管理，應依本法辦理。")
                for n in range(1, 9)
            ],
        )
        for i, (pcode, name) in enumerate(LAWS.items())
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())


def test_without_a_scope_it_returns_exactly_what_the_store_returns(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    tool = _tool()
    _import(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    queries = [f"{a}{b}" for a in THEMES for b in THEMES if a != b][:40]
    assert len(queries) >= 30
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.lexicon())
        for query in queries:
            terms = segmenter.to_tsvector_input(query)
            for vector, model in ((None, ""), (embedder.embed([query])[0], embedder.model)):
                expected = store.search(
                    terms,
                    tokenizer=segmenter.tokenizer_id,
                    as_of=ON,
                    limit=20,
                    embedding=vector,
                    model=model,
                )
                got = tool.scoped_search(
                    conn,
                    terms,
                    tokenizer=segmenter.tokenizer_id,
                    as_of=ON,
                    limit=20,
                    embedding=vector,
                    model=model,
                )
                assert got == expected, query


def test_a_scope_limits_the_pool_before_candidates_are_taken(
    conn: Conn, tmp_path: Path, store: OntologyStore
) -> None:
    lexicon = store.lexicon()
    tool = _tool()
    _import(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    scope = tool.resource_ids(conn, ["T0000002"])
    assert len(scope) == 1
    for query in ("廢水", "噪音 地震", "建物"):
        segmenter = Segmenter(lexicon)
        terms = segmenter.to_tsvector_input(query)
        hits = tool.scoped_search(
            conn,
            terms,
            tokenizer=segmenter.tokenizer_id,
            as_of=ON,
            limit=50,
            embedding=embedder.embed([query])[0],
            model=embedder.model,
            scope=scope,
        )
        assert hits and {h.law_id for h in hits} == {"T0000002"}
