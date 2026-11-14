"""Administrative rules (行政規則): loaded as immutable documents anchored on the text they
interpret, attached beside reasoning results by decision date, never premises of the reasoning."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite.ontology.v2 import reasoning_pb2, reasoning_pb2_grpc
from envcite_ontology.guidance import GuidanceError, load
from tests.conftest import call_metadata
from tests.test_annotation import LAW
from tests.test_grpc_reasoning import _case, _committed
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1
from tests.test_reasoning_transition import _amended

Conn = psycopg.Connection[tuple[object, ...]]
Stub = reasoning_pb2_grpc.ReasoningServiceStub


def _letter(no: str, issued_on: str, **over: Any) -> dict[str, Any]:
    return {
        "issuer": "環境部",
        "document_no": no,
        "issued_on": issued_on,
        "kind": "interpretation",
        "subject": f"{no}的主旨",
        "body": "說明：第一條所稱排放，包括間歇排放。",
        "interprets": [{"law": LAW, "path": "art_1", "text": "甲一"}],
        **over,
    }


def _doc(*letters: dict[str, Any], method: str = "human") -> dict[str, Any]:
    return {
        "derivation": {"method": method, "agent": "editor", "agent_version": "1"},
        "documents": list(letters),
    }


def _attached(conn: Conn, statement: int, decided: date) -> list[str]:
    return [
        str(no)
        for (no,) in conn.execute(
            "SELECT d.document_no FROM guidance.for_statements(%s, %s) AS g"
            "  JOIN guidance.document AS d ON d.id = g.document_id",
            ([statement], decided),
        )
    ]


def test_a_letter_is_anchored_on_the_text_it_interprets(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    assert (loaded.documents, loaded.interpretations, loaded.repeated) == (1, 1, 0)
    with pytest.raises(GuidanceError, match="現行任何版本"):
        load(
            conn,
            _doc(
                _letter(
                    "甲字第2號",
                    "2019-03-01",
                    interprets=[{"law": LAW, "path": "art_1", "text": "不是原文"}],
                )
            ),
        )


def test_loading_the_same_letter_again_changes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    again = load(conn, _doc(_letter("甲字第1號", "2019-03-01")))

    assert (again.repeated, again.interpretations) == (1, 0)
    assert _scalar(conn, "SELECT count(*) FROM guidance.document") == 1
    with pytest.raises(GuidanceError, match="內容與這次不同"):
        load(conn, _doc(_letter("甲字第1號", "2019-03-01", body="改過的說明")))


def test_a_model_does_not_load_administrative_rules(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(GuidanceError, match="not extracted by a model"):
        load(conn, _doc(_letter("甲字第1號", "2019-03-01"), method="llm"))


def test_a_later_letter_supersedes_from_its_issue_date(conn: Conn, tmp_path: Path) -> None:
    """釋字第 287 號：後函變更前釋示，前函自後函發文日起不再援用。"""
    duty, _ = _committed(conn, tmp_path)
    load(
        conn,
        _doc(
            _letter("甲字第1號", "2019-03-01"),
            _letter(
                "甲字第2號",
                "2019-05-01",
                supersedes=[{"issuer": "環境部", "document_no": "甲字第1號"}],
            ),
        ),
    )

    assert _attached(conn, duty, date(2019, 2, 1)) == []
    assert _attached(conn, duty, date(2019, 4, 1)) == ["甲字第1號"]
    assert _attached(conn, duty, date(2019, 5, 1)) == ["甲字第2號"]


def test_a_repeal_can_take_effect_after_it_is_issued(conn: Conn, tmp_path: Path) -> None:
    """公報「廢止「甲」，並自某日生效」：前函自後函載明的生效日起停止，不得早於後函發文日。"""
    duty, _ = _committed(conn, tmp_path)
    earlier = {"issuer": "環境部", "document_no": "甲字第1號"}
    load(
        conn,
        _doc(
            _letter("甲字第1號", "2019-03-01"),
            _letter("甲字第2號", "2019-05-01", supersedes=[{**earlier, "from": "2019-07-01"}]),
        ),
    )

    assert _attached(conn, duty, date(2019, 6, 30)) == ["甲字第1號", "甲字第2號"]
    assert _attached(conn, duty, date(2019, 7, 1)) == ["甲字第2號"]
    with pytest.raises(GuidanceError, match="ceases_after_successor"):
        load(
            conn,
            _doc(
                _letter("甲字第3號", "2019-01-01"),
                _letter(
                    "甲字第4號",
                    "2019-05-01",
                    supersedes=[{**earlier, "document_no": "甲字第3號", "from": "2019-04-01"}],
                ),
            ),
        )


def test_a_letter_can_cease_without_a_successor(conn: Conn, tmp_path: Path) -> None:
    duty, _ = _committed(conn, tmp_path)
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", ceased_on="2019-04-01")))

    assert _attached(conn, duty, date(2019, 3, 31)) == ["甲字第1號"]
    assert _attached(conn, duty, date(2019, 4, 1)) == []
    with pytest.raises(GuidanceError, match="ceases_after_issue"):
        load(conn, _doc(_letter("甲字第3號", "2019-03-01", ceased_on="2019-01-01")))


def test_a_letter_beside_the_result_follows_the_decision_date(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    """行為早於發文、處分在發文之後：解釋性規定闡明法規原意，未確定的案件即可附上。"""
    duty, _ = _committed(conn, tmp_path)
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", published_in="行政院公報第25卷")))

    response = reasoning_stub.Evaluate(_case(), metadata=call_metadata())  # 行為 2019-01-01

    (g,) = response.at_decision.guidance
    assert (g.document_no, g.issued_on, g.kind) == (
        "甲字第1號",
        "2019-03-01",
        reasoning_pb2.GUIDANCE_KIND_INTERPRETATION,
    )
    assert list(g.statement_ids) == [duty] and g.published_in == "行政院公報第25卷"
    early = reasoning_stub.Evaluate(_case(decided_on="2019-02-01"), metadata=call_metadata())
    assert list(early.at_decision.guidance) == []


def test_an_amended_text_drops_the_interpretation_of_the_old_one(
    conn: Conn, tmp_path: Path
) -> None:
    """解釋錨定文字：第二條修正（乙一 → 乙二）後，舊文字的解釋不附在新文字的陳述旁。"""
    _, new_limit = _amended(conn, tmp_path)
    old_limit = int(
        _scalar(
            conn,
            "SELECT id FROM annotation.statement WHERE id <> %s AND statement_kind"
            " = 'constitutive'",
            new_limit,
        )
    )
    interprets = [{"law": LAW, "path": "art_2", "text": "乙一"}]
    load(conn, _doc(_letter("甲字第1號", "2019-03-01", interprets=interprets)))

    assert _attached(conn, old_limit, date(2020, 6, 1)) == ["甲字第1號"]
    assert _attached(conn, new_limit, date(2020, 6, 1)) == []


def test_a_writer_adds_rules_but_cannot_rewrite_them(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    conn.execute("SET ROLE envcite_ontology_writer")
    try:
        load(conn, _doc(_letter("甲字第1號", "2019-03-01")))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE guidance.document SET body = '改寫'")
    finally:
        conn.rollback()
        conn.execute("RESET ROLE")


@pytest.mark.parametrize(
    ("columns", "violated"),
    [
        # 下架的停止適用要對到一個在停止日下架的函釋項目
        ("'taken_down', 'moenv_guidance', '甲字第1號', NULL, NULL", "taken_down_on_drop"),
        ("'taken_down', NULL, NULL, NULL, NULL", "taken_down_names_the_item"),
        ("'successor', NULL, NULL, NULL, NULL", "successor_names_the_rule"),
        ("'announced', NULL, NULL, %(id)s, '2019-03-01'", "successor_names_the_rule"),
        # 抄來的發文日要是那一件的發文日
        ("'successor', NULL, NULL, %(id)s, '2019-01-01'", "cessation_by_document_id_by_issued_on"),
    ],
)
def test_a_cessation_is_checked_by_the_database(
    conn: Conn, tmp_path: Path, columns: str, violated: str
) -> None:
    """停止適用的不變式是約束：繞過載入程式直接寫，也寫不進去。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_letter("甲字第1號", "2019-03-01"), _letter("甲字第2號", "2019-03-01")))
    ids = {
        str(no): id_ for no, id_ in conn.execute("SELECT document_no, id FROM guidance.document")
    }
    with pytest.raises(psycopg.errors.IntegrityError, match=violated):
        conn.execute(
            "INSERT INTO guidance.cessation (document_id, issued_on, ceased_on, cause,"
            " dropped_feed, dropped_key, by_document_id, by_issued_on)"
            f" SELECT %(me)s, '2019-03-01', '2019-04-01', {columns}",
            {"me": ids["甲字第1號"], "id": ids["甲字第2號"]},
        )
    conn.rollback()


@pytest.mark.parametrize(
    ("feed", "missed", "dropped_on"),
    [("moenv_guidance", 1, "2026-01-01"), ("moenv_guidance", 2, None), ("gazette", 1, None)],
)
def test_a_dropped_letter_has_missed_two_crawls(
    conn: Conn, feed: str, missed: int, dropped_on: str | None
) -> None:
    """下架只經 `guidance.settle_crawl`：連續兩次完整走訪沒出現；其他來源不計次。"""
    snapshot = _scalar(
        conn,
        "INSERT INTO source.snapshot"
        " (feed, data_date, sha256, size_bytes, location, license_notice)"
        " VALUES (%s, '2026-01-01', sha256('x'), 1, 'x', 'x') RETURNING id",
        feed,
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO source.item (feed, key, content_sha256, first_snapshot_id,"
            " last_snapshot_id, missed_crawls, dropped_on)"
            " VALUES (%s, 'k', sha256('x'), %s, %s, %s, %s)",
            (feed, snapshot, snapshot, missed, dropped_on),
        )
    conn.rollback()


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"kind": "decree"}, id="unknown-kind"),
        pytest.param({"subject": None}, id="null-subject"),
        pytest.param(
            {"interprets": [{"law": LAW, "path": "art_1"}]}, id="interpretation-without-text"
        ),
        pytest.param({"ceased_on": "soon"}, id="bad-ceased-on"),
    ],
)
def test_a_document_of_the_wrong_shape_is_rejected_before_anything_is_written(
    conn: Conn, tmp_path: Path, bad: dict[str, Any]
) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(GuidanceError, match="文件欄位缺漏或型別不合"):
        load(conn, _doc(_letter("甲字第1號", "2019-03-01", **bad)))
    assert _scalar(conn, "SELECT count(*) FROM guidance.document") == 0


def test_a_document_without_derivation_or_documents_is_rejected(conn: Conn) -> None:
    broken: list[dict[str, Any]] = [
        {},
        {"documents": []},
        {"derivation": {"method": "human"}, "documents": []},
    ]
    for doc in broken:
        with pytest.raises(GuidanceError, match="文件欄位缺漏或型別不合"):
            load(conn, doc)


def test_a_blank_repeal_date_means_the_successors_issue_date(conn: Conn, tmp_path: Path) -> None:
    duty, _ = _committed(conn, tmp_path)
    earlier = {"issuer": "環境部", "document_no": "甲字第1號"}
    load(
        conn,
        _doc(
            _letter("甲字第1號", "2019-03-01"),
            _letter("甲字第2號", "2019-05-01", supersedes=[{**earlier, "from": ""}]),
        ),
    )

    assert _attached(conn, duty, date(2019, 4, 30)) == ["甲字第1號"]
    assert _attached(conn, duty, date(2019, 5, 1)) == ["甲字第2號"]


def test_a_bad_issue_date_is_rejected_before_anything_is_written(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(GuidanceError, match="文件欄位缺漏或型別不合"):
        load(conn, _doc(_letter("甲字第1號", "someday")))
    assert _scalar(conn, "SELECT count(*) FROM guidance.document") == 0
