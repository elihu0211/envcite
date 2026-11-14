"""0053：點名母法時，檢索範圍納入依其授權訂定的命令（命令授權的命令也算）。"""

from pathlib import Path

import psycopg

from envcite_ontology import delegation, projection
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


def _command(pcode: str, title: str, articles: list[tuple[str, str]]) -> dict[str, object]:
    return {**_law(pcode, title, COMMAND_HISTORY, "20180620", articles), "LawLevel": "命令"}


def _corpus(conn: Conn, tmp_path: Path) -> None:
    laws = [
        _law(
            "T0000001",
            "廢水管理法",
            HISTORY,
            "20180613",
            [("1", "為管理廢水，特制定本法。"), ("2", "排放廢水者應申報。")],
        ),
        _command(
            "T0000002",
            "廢水管理法施行細則",
            [
                ("1", "本細則依廢水管理法第二條規定訂定之。"),
                ("2", "申報應於每季終了後十五日內為之。"),
            ],
        ),
        _command(
            "T0000003",
            "廢水申報作業要點辦法",
            [
                ("1", "本辦法依廢水管理法施行細則第二條規定訂定之。"),
                ("2", "申報書表應載明排放量。"),
            ],
        ),
        _law("T0000004", "噪音管制法", HISTORY, "20180613", [("1", "噪音之申報依本法。")]),
    ]
    stored_snapshot(conn, tmp_path, laws)
    importer.run(conn, tmp_path, Moj())
    delegation.extract(conn)
    projection.rebuild(conn)
    conn.commit()


def test_naming_the_statute_searches_its_commands_down_the_chain(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        terms = segmenter.to_tsvector_input("申報")
        hits = store.retrieval.search(
            terms, tokenizer=segmenter.tokenizer_id, as_of=ON, laws=["T0000001"]
        )
        assert {h.local_id for h in hits} == {"T0000001", "T0000002", "T0000003"}


def test_naming_a_command_does_not_climb_to_its_statute(
    conn: Conn, tmp_path: Path, dsn: str
) -> None:
    _corpus(conn, tmp_path)
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        terms = segmenter.to_tsvector_input("申報")
        hits = store.retrieval.search(
            terms, tokenizer=segmenter.tokenizer_id, as_of=ON, laws=["T0000002"]
        )
        assert {h.local_id for h in hits} == {"T0000002", "T0000003"}
