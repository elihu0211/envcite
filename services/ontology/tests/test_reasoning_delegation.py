"""授權鏈：命令的陳述回溯到法律的授權；鏈斷了，命令所定的罰則與處罰命令義務的罰則附條件。"""

from datetime import date
from pathlib import Path
from typing import Any

from psycopg.types.range import Range

from envcite_ontology import delegation
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.annotate import load
from envcite_ontology.ingest import importer
from envcite_ontology.reasoning import Result, evaluate
from tests.reasoning_support import Conn, _scope, _with
from tests.sample import stored_snapshot
from tests.test_annotation import _doc
from tests.test_import import _law, _scalar
from tests.test_incremental import V1, _articles

ON = date(2019, 1, 1)
MOTHER, COMMAND, OTHER = "T0000002", "T0000003", "T0000004"
CASE: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1, "ap_intent": 1}


def _corpus(conn: Conn, tmp_path: Path, first: str, *, other: str | None = None) -> None:
    """母法（法律：第 1 條授權、第 2 條罰則）與命令（首條 `first`、第 2 條義務、第 3 條罰則）。"""
    laws = [
        _law(
            MOTHER, "母法", LawHistories=V1, LawArticles=_articles("授權訂定辦法", "違反者處罰鍰")
        ),
        _law(
            COMMAND,
            "子辦法",
            LawLevel="命令",
            LawHistories=V1,
            LawArticles=_articles(first, "甲一", "乙一"),
        ),
    ]
    if other is not None:
        laws.append(
            _law(OTHER, "他辦法", LawLevel="命令", LawHistories=V1, LawArticles=_articles(other))
        )
    stored_snapshot(conn, tmp_path, laws)
    assert importer.run(conn, tmp_path, Moj()).actions == {"added": len(laws)}
    delegation.extract(conn)


def _statements(conn: Conn) -> dict[str, int]:
    def fine(name: str, law: str, path: str, text: str) -> dict[str, Any]:
        return {
            "id": name,
            "law": law,
            "path": path,
            "text": text,
            "kind": "sanction",
            "conditions": [_scope("serious")],
            "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
        }

    duty = {
        "id": "duty",
        "law": COMMAND,
        "path": "art_2",
        "text": "甲一",
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            _scope("discharging"),
            {"role": "duty", "clause": 1, "concept": "cod", "comparator": "le", "threshold": 100},
        ],
    }
    loaded = load(
        conn,
        _with(
            _doc(
                duty,
                fine("own", COMMAND, "art_3", "乙一"),
                fine("statute", MOTHER, "art_2", "違反者處罰鍰"),
            ),
            "serious",
        ),
    )
    return dict(loaded.ids)


def _conditional(result: Result) -> dict[int, bool]:
    return {p.statement_id: p.conditional for p in result.penalties}


def test_a_command_traced_to_its_statute_is_rooted(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, "本辦法依母法第一條規定訂定之。")
    ids = _statements(conn)

    result = evaluate(conn, ON, CASE)

    assert result.verdicts[ids["duty"]] == "violation"
    assert _conditional(result) == {ids["own"]: False, ids["statute"]: False}
    chain = result.delegation[ids["duty"]]
    assert chain.rooted
    (link,) = chain.links
    assert (link.command_law_id, link.cited_path, link.law_id, link.gap) == (
        COMMAND,
        "art_1",
        MOTHER,
        None,
    )
    assert link.version_iri is not None
    assert ids["statute"] not in result.delegation  # 法律的陳述沒有授權鏈


def _broken(result: Result, ids: dict[str, int]) -> tuple[str | None, ...]:
    """鏈斷了：義務的結論不變，兩則罰則都附條件；回傳各環節的缺口。"""
    assert result.verdicts[ids["duty"]] == "violation"
    assert _conditional(result) == {ids["own"]: True, ids["statute"]: True}
    chain = result.delegation[ids["duty"]]
    assert not chain.rooted
    return tuple(link.gap for link in chain.links)


def test_a_command_without_a_citation_is_pending(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, "為辦理本事項，特訂定本辦法。")
    ids = _statements(conn)

    assert _broken(evaluate(conn, ON, CASE), ids) == ("no_citation",)


def test_a_citation_that_resolves_to_nothing_is_pending(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, "本辦法依母法第九條規定訂定之。")
    ids = _statements(conn)

    assert _broken(evaluate(conn, ON, CASE), ids) == ("unresolved_law",)


def test_an_authorizing_article_out_of_force_breaks_the_chain(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, "本辦法依母法第一條規定訂定之。")
    ids = _statements(conn)
    conn.execute(
        "UPDATE corpus.provision_version AS v"
        " SET force_period = %s, commencement_rule = 'unchanged'"
        "  FROM corpus.provision AS p JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE v.provision_id = p.id AND r.local_id = %s AND p.path = 'art_1'",
        (Range(date(2018, 8, 1), date(2018, 12, 1), "[)"), MOTHER),
    )

    assert evaluate(conn, date(2018, 11, 1), CASE).delegation[ids["duty"]].rooted
    assert _broken(evaluate(conn, ON, CASE), ids) == ("law_not_in_force",)


def test_an_invalidated_authorizing_article_breaks_the_chain(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path, "本辦法依母法第一條規定訂定之。")
    ids = _statements(conn)
    ruling = _scalar(
        conn,
        "INSERT INTO corpus.constitutional_ruling"
        " (ruling_no, ruling_kind, decided_on, source_url, excerpt)"
        " VALUES ('釋字第 998 號', 'interpretation', '2018-10-01', 'https://cons.judicial.gov.tw/x',"
        " '失其效力') RETURNING id",
    )
    conn.execute(
        "INSERT INTO corpus.ruling_invalidation"
        " (ruling_id, resource_id, provision_id, invalid_from)"
        " SELECT %s, p.resource_id, p.id, '2018-12-01' FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE r.local_id = %s AND p.path = 'art_1'",
        (ruling, MOTHER),
    )

    assert evaluate(conn, date(2018, 11, 30), CASE).delegation[ids["duty"]].rooted
    assert _broken(evaluate(conn, ON, CASE), ids) == ("law_invalidated",)


def test_a_command_delegated_by_a_command_walks_up_to_the_statute(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(
        conn,
        tmp_path,
        "本辦法依他辦法第一條規定訂定之。",
        other="本辦法依母法第一條規定訂定之。",
    )
    ids = _statements(conn)

    chain = evaluate(conn, ON, CASE).delegation[ids["duty"]]

    assert chain.rooted
    assert [(link.command_law_id, link.law_id, link.gap) for link in chain.links] == [
        (COMMAND, OTHER, None),
        (OTHER, MOTHER, None),
    ]


def test_commands_that_delegate_to_each_other_never_reach_a_statute(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(
        conn,
        tmp_path,
        "本辦法依他辦法第一條規定訂定之。",
        other="本辦法依子辦法第一條規定訂定之。",
    )
    ids = _statements(conn)

    gaps = _broken(evaluate(conn, ON, CASE), ids)

    assert gaps == (None, "cycle")
