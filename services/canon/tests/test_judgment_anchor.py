"""Judgment reasons yield only the articles the court applied, on a version the text names."""

from datetime import date
from pathlib import Path

from canon.db import Conn
from canon.import_problems import Problem
from canon.law.judgment import load
from canon.law.judgment_anchor import RULE_VERSION, extract
from tests.test_annotation import LAW
from tests.test_incremental import _first, _law_v1, _law_v2, _next

DECIDED = date(2021, 1, 1)  # 第 2 條已是「乙二」
CONDUCT = date(2019, 6, 1)  # 第 2 條仍是「乙一」


def _ready(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    _next(conn, tmp_path, _law_v2())


def _cite(path: str, text: str, basis: str) -> dict[str, str]:
    return {"law": LAW, "path": path, "text": text, "basis": basis}


def test_an_applied_article_anchors_on_the_text_in_force_on_the_decision_date(
    conn: Conn, tmp_path: Path
) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "理由：本件適用噪音管制法第二條。", DECIDED)

    assert found.problems == ()
    assert found.cites == (_cite("art_2", "乙二", "decided_on"),)
    assert RULE_VERSION == 1


def test_a_named_prior_version_anchors_on_the_predecessor(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "依修正前噪音管制法第二條，限值為一百。", DECIDED)

    assert found.cites == (_cite("art_2", "乙一", "stated"),)


def test_conduct_time_needs_exactly_one_date(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    dated = extract(conn, "行為時（中華民國一百零八年六月一日）適用噪音管制法第二條。", DECIDED)
    arabic = extract(conn, "裁處時108年6月1日適用噪音管制法第二條。", DECIDED)
    undated = extract(conn, "行為時適用噪音管制法第二條。", DECIDED)
    both = extract(
        conn,
        "行為時（中華民國一百零八年六月一日、中華民國一百零九年四月一日）適用噪音管制法第二條。",
        DECIDED,
    )

    assert dated.cites == arabic.cites == (_cite("art_2", "乙一", "stated"),)
    assert [p.code for p in undated.problems] == ["version_not_unique"]
    assert undated.cites == ()
    assert [p.code for p in both.problems] == ["version_not_unique"]


def test_a_version_phrase_that_points_two_ways_anchors_nothing(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "依修正前行為時之噪音管制法第二條。", DECIDED)

    assert found.cites == ()
    assert found.problems == (Problem("warning", "version_not_unique", "噪音管制法art_2"),)


def test_comparison_and_non_application_do_not_anchor(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(
        conn,
        "參照噪音管制法第二條之規定。本件不適用噪音管制法第一條。本件適用噪音管制法第一條。",
        DECIDED,
    )

    assert found.cites == (_cite("art_1", "甲一", "decided_on"),)
    assert [p.code for p in found.problems] == ["comparative_mention"]


def test_an_unknown_name_or_article_is_not_anchored(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "適用海洋污染防治法第九條。適用噪音管制法第九十九條。", DECIDED)

    assert found.cites == ()
    assert [p.code for p in found.problems] == ["law_unresolved", "provision_unresolved"]


def test_no_version_on_the_decision_date_anchors_nothing(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "適用噪音管制法第二條。", date(2000, 1, 1))

    assert found.cites == ()
    assert [p.code for p in found.problems] == ["no_version_on_decision"]


def test_listed_articles_share_the_law_name(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)

    found = extract(conn, "依噪音管制法第二條及第一條。", DECIDED)

    assert found.cites == (
        _cite("art_2", "乙二", "decided_on"),
        _cite("art_1", "甲一", "decided_on"),
    )


def test_extracted_cites_load_as_anchors(conn: Conn, tmp_path: Path) -> None:
    _ready(conn, tmp_path)
    found = extract(conn, "依修正前噪音管制法第二條。", DECIDED)

    load(
        conn,
        {
            "derivation": {"method": "rule", "agent": "judicial-sync", "agent_version": "1"},
            "decisions": [
                {
                    "jid": "TPAA,110,上,1,20210101,1",
                    "decided_on": "2021-01-01",
                    "instance": "supreme",
                    "title": "噪音管制法",
                    "body": "依修正前噪音管制法第二條。",
                    "cites": list(found.cites),
                }
            ],
        },
    )

    assert conn.execute(
        "SELECT basis::text FROM judgment.anchor AS a"
        " JOIN corpus.provision AS p ON p.id = a.provision_id"
    ).fetchall() == [("stated",)]
