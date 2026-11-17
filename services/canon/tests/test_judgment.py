"""Administrative court decisions (行政法院裁判): interpretive basis anchored on the text the court
applied, replaced by jid, and deleted down to a tombstone when the source no longer has them."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from canon.annotate import load as load_annotations
from canon.db import Conn
from canon.law.judgment import JudgmentError, load, take_down
from tests.test_annotation import LAW, _duty, _limit
from tests.test_annotation import _doc as annotation_doc
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1, _law_v2, _next

JID = "TPAA,110,上,1,20210101,1"  # 最高行政法院；類別碼 A


def _decision(jid: str = JID, **over: Any) -> dict[str, Any]:
    return {
        "jid": jid,
        "decided_on": "2021-01-01",
        "instance": "supreme",
        "title": "廢棄物清理法",
        "body": "理由：依第一條，間歇排放亦屬排放。",
        "cites": [{"law": LAW, "path": "art_1", "text": "甲一", "basis": "decided_on"}],
        **over,
    }


def _doc(*decisions: dict[str, Any], method: str = "rule") -> dict[str, Any]:
    return {
        "derivation": {"method": method, "agent": "judicial-sync", "agent_version": "1"},
        "decisions": list(decisions),
    }


def _count(conn: Conn, table: str) -> int:
    return int(_scalar(conn, f"SELECT count(*) FROM judgment.{table}"))


def test_a_decision_is_anchored_on_the_text_the_court_applied(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    loaded = load(conn, _doc(_decision()))

    assert (loaded.decisions, loaded.repeated, loaded.replaced, loaded.anchors) == (1, 0, 0, 1)
    assert _scalar(conn, "SELECT kind::text FROM judgment.decision") == "ordinary"
    with pytest.raises(JudgmentError, match="任何版本"):
        load(
            conn,
            _doc(
                _decision(
                    "TPAA,110,上,2,20210101,1",
                    cites=[{"law": LAW, "path": "art_1", "text": "不是原文", "basis": "stated"}],
                )
            ),
        )
    assert _count(conn, "decision") == 1


def test_a_decision_may_be_anchored_on_a_superseded_text(conn: Conn, tmp_path: Path) -> None:
    """Courts apply the text at the time of conduct or disposition, not today's."""
    _first(conn, tmp_path, _law_v1())
    _next(conn, tmp_path, _law_v2())  # 第 2 條 2020-03-01 起為「乙二」
    old = {"law": LAW, "path": "art_2", "text": "乙一"}

    # 理由載明適用修正前條文：舊文字也可以錨定
    load(conn, _doc(_decision(cites=[{**old, "basis": "stated"}])))
    # 裁判日在修正前：舊文字是裁判日現行
    load(
        conn,
        _doc(
            _decision(
                "TPAA,108,上,3,20190601,1",
                decided_on="2019-06-01",
                cites=[{**old, "basis": "decided_on"}],
            )
        ),
    )
    # 理由未載明版本、裁判日在修正後：舊文字不是裁判日現行，不猜測，整份拒絕
    with pytest.raises(JudgmentError, match="裁判日 2021-01-01"):
        load(
            conn,
            _doc(_decision("TPAA,110,上,4,20210101,1", cites=[{**old, "basis": "decided_on"}])),
        )
    assert _count(conn, "decision") == 2
    assert _count(conn, "anchor") == 2


def test_only_administrative_decisions_are_stored(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    with pytest.raises(JudgmentError, match="不是行政類"):
        load(conn, _doc(_decision("TPSM,110,台上,1,20210101,1")))  # 最高法院刑事庭
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO judgment.decision (jid, decided_on, instance, title, body, derivation_id)"
            " VALUES ('TPSM,1', '2021-01-01', 'supreme', 't', 'b', 1)"
        )
    conn.rollback()
    assert _count(conn, "decision") == 0


def test_a_grand_chamber_ruling_is_only_the_supreme_courts(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    load(conn, _doc(_decision(kind="grand_chamber")))

    assert _scalar(conn, "SELECT kind::text FROM judgment.decision") == "grand_chamber"
    with pytest.raises(JudgmentError, match="大法庭設於最高行政法院"):
        load(
            conn, _doc(_decision("TPBA,110,訴,5,20210101,1", kind="grand_chamber", instance="high"))
        )


def test_a_model_does_not_load_decisions(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    with pytest.raises(JudgmentError, match="not extracted by a model"):
        load(conn, _doc(_decision(), method="llm"))


def test_the_same_jid_is_the_same_decision(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_decision()))
    derivations = _scalar(conn, "SELECT count(*) FROM annotation.derivation")

    again = load(conn, _doc(_decision()))

    assert (again.repeated, again.replaced, again.anchors) == (1, 0, 0)
    assert _scalar(conn, "SELECT count(*) FROM annotation.derivation") == derivations

    changed = load(
        conn,
        _doc(
            _decision(
                body="更正後的理由。",
                cites=[
                    {"law": LAW, "path": "art_1", "text": "甲一", "basis": "decided_on"},
                    {"law": LAW, "path": "art_2", "text": "乙一", "basis": "stated"},
                ],
            )
        ),
    )

    assert (changed.repeated, changed.replaced, changed.anchors) == (0, 1, 2)
    assert _count(conn, "decision") == 1 and _count(conn, "anchor") == 2
    assert _scalar(conn, "SELECT body FROM judgment.decision") == "更正後的理由。"

    fewer = load(conn, _doc(_decision(body="更正後的理由。", cites=[])))  # 不再引用任何條文

    assert fewer.replaced == 1 and _count(conn, "anchor") == 0


def test_a_taken_down_decision_leaves_only_its_tombstone(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    other = "TPAA,110,上,9,20210101,1"
    load(conn, _doc(_decision(), _decision(other)))

    gone = take_down(conn, [JID, JID], date(2026, 11, 16))

    assert (gone.deleted, gone.tombstoned) == (1, 1)
    assert _count(conn, "decision") == 1
    assert _scalar(conn, "SELECT jid FROM judgment.decision") == other
    assert _count(conn, "anchor") == 1  # 另一筆的錨定邊還在
    assert conn.execute("SELECT jid, detected_on FROM judgment.takedown").fetchall() == [
        (JID, date(2026, 11, 16))
    ]
    # 墓碑不含內容：只有 jid 與偵測日兩欄
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_schema = 'judgment' AND table_name = 'takedown'",
        )
        == 2
    )


def test_taking_down_again_keeps_the_first_detection_day(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    take_down(conn, ["TPAA,110,上,7,20210101,1"], date(2026, 11, 16))  # 從未載入也記墓碑
    again = take_down(conn, ["TPAA,110,上,7,20210101,1"], date(2026, 11, 20))

    assert (again.deleted, again.tombstoned) == (0, 0)
    assert _scalar(conn, "SELECT detected_on FROM judgment.takedown") == date(2026, 11, 16)
    with pytest.raises(JudgmentError, match="不可為空"):
        take_down(conn, [""], date(2026, 11, 16))


def test_a_decision_that_reappears_is_no_longer_taken_down(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_decision()))
    take_down(conn, [JID], date(2026, 11, 16))

    load(conn, _doc(_decision()))

    assert _count(conn, "decision") == 1 and _count(conn, "takedown") == 0


def test_a_decision_is_attached_to_the_statements_of_the_text_it_applied(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    first = load_annotations(conn, annotation_doc(_duty(), _limit("乙一", 100)))
    _next(conn, tmp_path, _law_v2())
    second = load_annotations(conn, annotation_doc(_limit("乙二", 80)))
    duty, old, new = first.ids["duty"], first.ids["limit100"], second.ids["limit80"]
    load(
        conn,
        _doc(
            _decision(
                cites=[
                    {"law": LAW, "path": "art_1", "text": "甲一", "basis": "decided_on"},
                    {"law": LAW, "path": "art_2", "text": "乙一", "basis": "stated"},
                ]
            )
        ),
    )

    def attached(on: date) -> list[tuple[object, object]]:
        return [
            (d, s)
            for d, s in conn.execute(
                "SELECT decision_id, statement_id FROM judgment.for_statements(%s, %s)"
                " ORDER BY statement_id",
                ([duty, old, new], on),
            )
        ]

    decision = _scalar(conn, "SELECT id FROM judgment.decision")
    # 附到裁判所讀的文字：修正後的第 2 條（乙二）沒有裁判
    assert attached(date(2021, 1, 1)) == [(decision, duty), (decision, old)]
    assert attached(date(2020, 12, 31)) == []  # 裁判日之前不附


def test_taking_down_removes_what_hangs_on_the_decision(conn: Conn, tmp_path: Path) -> None:
    """Derived rows must cascade from the decision so a takedown needs no per-table cleanup."""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_decision()))
    referencing = conn.execute(
        "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint"
        " WHERE contype = 'f' AND confrelid = 'judgment.decision'::regclass"
    ).fetchall()

    assert referencing == [("judgment.anchor", "c")]


def test_only_the_writer_may_replace_or_delete_decisions(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_decision()))

    conn.execute("SET ROLE canon_reader")
    assert _count(conn, "decision") == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM judgment.decision")
    conn.rollback()

    conn.execute("SET ROLE canon_reviewer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE judgment.decision SET body = '改'")
    conn.rollback()

    conn.execute("SET ROLE canon_writer")
    taken = take_down(conn, [JID], date(2026, 11, 16))
    assert (taken.deleted, taken.tombstoned) == (1, 1)
