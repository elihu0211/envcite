"""The retrieval evaluation tool: questions that copy their article are dropped, and the report
measures both arms and derives a floor per score."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import psycopg

from envcite_ontology import projection
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter
from tests.sample import stored_snapshot
from tests.test_hybrid import FakeEmbedder
from tests.test_import import _law
from tests.test_incremental import _articles, _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]
TOOL = Path(__file__).resolve().parents[1] / "tools" / "retrieval_eval.py"
NOISE = "管制區內任何人不得於夜間製造噪音，違者由主管機關限期改善並得按次處罰以維護居民安寧"
WATER = "事業排放廢水於地面水體者，應符合放流水標準；主管機關得依水體特性另定較嚴之標準管制"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("retrieval_eval", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["retrieval_eval"] = module
    spec.loader.exec_module(module)
    return module


class Writer:
    name = "fake:writer"

    def complete(self, prompt: str) -> str:
        if "日常問題" in prompt:
            return "紅燒肉要燉多久？\n\n怎麼練習倒立？\n"
        if "夜間製造噪音" in prompt:
            return "晚上鄰居一直很吵可以檢舉嗎？"
        return prompt.rsplit("條文：\n", 1)[1]  # 照抄條文


def test_overlap_counts_the_question_pairs_found_in_the_article() -> None:
    tool = _tool()
    assert tool.overlap("排放廢水", WATER) == 1.0
    assert tool.overlap("晚上很吵", NOISE) == 0.0


def test_a_question_that_copies_its_article_is_dropped(conn: Conn, tmp_path: Path) -> None:
    tool = _tool()
    stored_snapshot(
        conn,
        tmp_path,
        [
            _law("T0000001", "噪音管制法", LawArticles=_articles(NOISE)),
            _law("T0000002", "水污染防治法", LawArticles=_articles(WATER)),
        ],
    )
    assert importer.run(conn, tmp_path, Moj()).actions == {"added": 2}

    items = tool.generate(conn, Writer(), count=5, negatives=2, seed="s", on=date(2019, 6, 1))

    answerable = [i for i in items if i["kind"] == "answerable"]
    assert [(i["article"], i["question"]) for i in answerable] == [
        ("1", "晚上鄰居一直很吵可以檢舉嗎？")
    ]
    assert [i["question"] for i in items if i["kind"] == "out_of_scope"] == [
        "紅燒肉要燉多久？",
        "怎麼練習倒立？",
    ]


def test_the_floor_separates_answerable_from_out_of_scope() -> None:
    tool = _tool()
    assert tool.best_floor([0.8, 0.7, 0.6], [0.2, 0.65]) == (0.7, 2 / 3, 0.0)


def test_the_report_measures_both_arms(conn: Conn, tmp_path: Path, dsn: str) -> None:
    tool = _tool()
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(NOISE, WATER)))
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    items = [
        {"kind": "answerable", "question": "廢水", "law_id": "T0000001", "article": "2"},
        {"kind": "answerable", "question": "聲響", "law_id": "T0000001", "article": "1"},
        {"kind": "out_of_scope", "question": "紅燒肉"},
    ]
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.lexicon())
        lexical = tool.search_all(store, segmenter, items, None, k=10, on="2019-06-01")
        hybrid = tool.search_all(store, segmenter, items, embedder, k=10, on="2019-06-01")

    assert [o.rank for o in lexical] == [1, None, None]
    assert [o.rank for o in hybrid][:2] == [1, 1]  # 「聲響」只有語意臂找得到
    text = tool.report(items, lexical, hybrid)
    assert "| 字面臂 | 0.500 |" in text and "| 混合 | 1.000 |" in text
    assert "semantic_similarity" in text
