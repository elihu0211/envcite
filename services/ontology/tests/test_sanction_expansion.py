"""0056：命中義務條，或依其授權訂定的命令，帶出處罰它的罰則條。"""

from pathlib import Path

import psycopg

from envcite_ontology import delegation, projection, violation
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_segmenter import Segmenter
from tests.sample import _law, stored_snapshot

Conn = psycopg.Connection[tuple[object, ...]]

ON = "2019-06-01"
HISTORY = "1.中華民國一百零七年六月十三日總統令制定公布全文 9 條；並自公布日施行"
COMMAND_HISTORY = (
    "1.中華民國一百零七年六月二十日行政院環境保護署令訂定發布全文 3 條；並自發布日施行"
)


def _corpus(conn: Conn, tmp_path: Path) -> None:
    command = {
        **_law(
            "T0000002",
            "廢水申報辦法",
            COMMAND_HISTORY,
            "20180620",
            [
                ("1", "本辦法依廢水管理法第二條第二項規定訂定之。"),
                ("2", "水質水量資料應每半年申報。"),
            ],
        ),
        "LawLevel": "命令",
    }
    laws = [
        _law(
            "T0000001",
            "廢水管理法",
            HISTORY,
            "20180613",
            [
                ("1", "為管理廢水，特制定本法。"),
                ("2", "事業應設置處理設施。\r\n前項申報之辦法，由中央主管機關定之。"),
                ("3", "事業應保持排放口暢通。"),
                (
                    "8",
                    "違反依第二條第二項所定辦法者，處新臺幣六千元以上罰鍰。",
                ),
                ("9", "違反第三條規定者，處新臺幣一萬元以上罰鍰。"),
            ],
        ),
        command,
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())
    delegation.extract(conn)
    violation.extract(conn)
    projection.rebuild(conn)
    conn.commit()


def _ids(store: OntologyStore, segmenter: Segmenter, query: str) -> list[str]:
    terms = segmenter.to_tsvector_input(query)
    hits = store.retrieval.search(terms, tokenizer=segmenter.tokenizer_id, as_of=ON, limit=10)
    return [f"{h.local_id}-{h.number}" for h in hits]


def test_a_duty_hit_brings_its_sanction_right_after_it(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        assert _ids(store, segmenter, "排放口暢通") == ["T0000001-3", "T0000001-9"]


def test_a_command_hit_brings_the_sanction_for_its_authorizing_article(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        assert _ids(store, segmenter, "水質水量資料") == ["T0000002-2", "T0000001-8"]


def test_the_brought_sanction_carries_the_score_of_what_brought_it(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        terms = segmenter.to_tsvector_input("排放口暢通")
        duty, sanction = store.retrieval.search(
            terms, tokenizer=segmenter.tokenizer_id, as_of=ON, limit=10
        )
        assert sanction.lexical_score == duty.lexical_score > 0
