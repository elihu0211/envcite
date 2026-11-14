"""義務的主體可以「未明示」，上位的適用情形標為「承自上位」：資料庫守住兩者的形狀。"""

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from envcite_ontology import annotation_read
from envcite_ontology.annotate import AnnotationError, load
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.model_gates.render import render_annotation
from tests.test_annotation import LAW, _doc, _duty
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_id, _law_v1
from tests.verification_support import EXTRACTED, Conn, FakeModel

ON = date(2019, 1, 1)


def _with_slots(slots: dict[str, Any]) -> dict[str, Any]:
    return _doc(_duty() | {"slots": slots}, review="extracted")


def test_an_unstated_subject_is_stored_as_a_row_without_a_value(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _with_slots({"subject": {"unstated": True}}))

    row = conn.execute(
        "SELECT slot_role::text, concept_id, literal, liability_basis, unstated"
        " FROM annotation.statement_slot"
    ).fetchone()
    assert row == ("subject", None, None, None, True)


@pytest.mark.parametrize(
    "slots",
    [
        pytest.param({"action": {"unstated": True}}, id="only-the-subject-can-be-unstated"),
        pytest.param({"subject": {"unstated": True, "literal": "事業"}}, id="no-value"),
        pytest.param({"subject": {"unstated": True, "liability": "act"}}, id="no-liability"),
        pytest.param(
            {"subject": [{"unstated": True}, {"literal": "事業"}]}, id="no-other-alternative"
        ),
        pytest.param(
            {"subject": [{"literal": "事業"}, {"unstated": True}]}, id="no-other-alternative-after"
        ),
        pytest.param({"subject": {}}, id="a-slot-has-a-value-or-is-unstated"),
    ],
)
def test_the_database_refuses_a_slot_that_is_unstated_and_something_else(
    conn: Conn, tmp_path: Path, slots: dict[str, Any]
) -> None:
    _first(conn, tmp_path, _law_v1())

    with pytest.raises(AnnotationError):
        load(conn, _with_slots(slots))
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement_slot") == 0


def _carried(**changes: Any) -> dict[str, Any]:
    return {
        "role": "scope",
        "clause": 1,
        "concept": "ctx_zone",
        "comparator": "eq",
        "threshold": 1,
        "carried": True,
    } | changes


def _with_carried(**changes: Any) -> dict[str, Any]:
    doc = _doc(deepcopy(_duty()), review="extracted")
    doc["concepts"].append({"code": "ctx_zone", "label": "在水污染管制區內"})
    doc["statements"][0]["conditions"].append(_carried(**changes))
    return doc


def test_a_carried_condition_is_marked_and_shown_to_the_reviewers(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _with_carried())

    assert _scalar(conn, "SELECT count(*) FROM annotation.statement_condition WHERE carried") == 1
    shown = render_annotation(conn, loaded.ids["duty"])
    assert "[ctx_zone] 在水污染管制區內 成立（承自上位）" in shown


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"role": "duty"}, id="only-scope"),
        pytest.param({"comparator": "le", "threshold": 3}, id="only-holds-or-not"),
        pytest.param({"negated": True}, id="not-negated"),
        pytest.param({"judgement": True}, id="not-a-judgement"),
    ],
)
def test_the_database_refuses_a_carried_condition_that_is_more_than_a_plain_scope(
    conn: Conn, tmp_path: Path, changes: dict[str, Any]
) -> None:
    _first(conn, tmp_path, _law_v1())

    with pytest.raises(AnnotationError):
        load(conn, _with_carried(**changes))


def test_two_statements_that_differ_only_in_an_unstated_subject_are_not_duplicates(
    conn: Conn, tmp_path: Path
) -> None:
    """指紋含「未明示」：沒寫主體與明說未明示是兩種標註，不被當成重複而吞掉。"""
    _first(conn, tmp_path, _law_v1())
    plain = load(conn, _doc(deepcopy(_duty()), review="extracted"))
    stated = load(conn, _with_slots({"subject": {"unstated": True}}))

    assert plain.ids["duty"] != stated.ids["duty"]
    assert stated.duplicates == 0


def _extract(conn: Conn, tmp_path: Path, subject: Any) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    doc["statements"][0]["slots"] = {"subject": subject}
    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )
    assert out.loaded is not None and out.loaded.statements == 1


def test_a_subject_the_text_does_not_write_is_stored_as_unstated(
    conn: Conn, tmp_path: Path
) -> None:
    _extract(conn, tmp_path, {"literal": "任何人"})

    assert _scalar(conn, "SELECT unstated FROM annotation.statement_slot") is True
    assert _scalar(conn, "SELECT literal FROM annotation.statement_slot") is None


def test_a_subject_the_text_writes_is_kept(conn: Conn, tmp_path: Path) -> None:
    _extract(conn, tmp_path, {"literal": "排放廢水者"})

    assert _scalar(conn, "SELECT unstated FROM annotation.statement_slot") is False
    assert _scalar(conn, "SELECT literal FROM annotation.statement_slot") == "排放廢水者"


def test_the_reviewers_are_told_the_subject_was_not_stated(conn: Conn, tmp_path: Path) -> None:
    _extract(conn, tmp_path, {"unstated": True})

    sid = int(_scalar(conn, "SELECT id FROM annotation.statement"))
    assert "主體：（原文未明示）" in render_annotation(conn, sid)


def test_the_api_has_no_slot_for_an_unstated_subject(conn: Conn, tmp_path: Path) -> None:
    """契約的槽位不是「某個值」就是缺席；未明示對呼叫端就是沒有主體槽位。"""
    _extract(conn, tmp_path, {"unstated": True})
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE assertion_kind = 'statement'"
    )

    listed = annotation_read.list_statements(conn, _law_id(conn), ON)

    assert [list(s.slots) for s in listed.statements] == [[]]


LEAD_IN = "在水污染管制區內，不得有下列行為："
ITEM = "一、使用農藥或化學肥料，致有污染主管機關指定之水體之虞。"
ITEM_PATH = "art_2/para_1/pt_1"


def _item_answer() -> dict[str, Any]:
    return {
        "concepts": [
            {"code": "pesticide", "label": "使用農藥或化學肥料"},
            {"code": "polluting", "label": "致有污染水體之虞"},
        ],
        "statements": [
            {
                "id": "s1",
                "quote": "使用農藥或化學肥料，致有污染主管機關指定之水體之虞。",
                "kind": "prescriptive",
                "deontic": "prohibition",
                "conditions": [
                    {"role": "duty", "clause": 1, "concept": "pesticide", "comparator": "eq"},
                    {"role": "duty", "clause": 1, "concept": "polluting", "comparator": "eq"},
                ],
            }
        ],
    }


def _extract_item(conn: Conn, tmp_path: Path, answer: dict[str, Any]) -> int:
    _first(
        conn,
        tmp_path,
        _law_v1(LawArticles=_articles("甲一", f"{LEAD_IN}\r\n{ITEM}")),
    )
    for condition in answer["statements"][0]["conditions"]:
        condition["threshold"] = 1
    out = extract_unit(
        conn,
        FakeModel(json.dumps(answer, ensure_ascii=False)),
        LAW,
        ITEM_PATH,
        agent="fake-model",
        agent_version="1",
    )
    assert out.loaded is not None
    return out.loaded.ids["s1"]


def test_an_item_gets_the_lead_in_condition_even_if_the_model_wrote_none(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extract_item(conn, tmp_path, _item_answer())

    row = conn.execute(
        "SELECT k.name, c.condition_role::text, c.carried FROM annotation.statement_condition AS c"
        "  JOIN vocab.concept AS k ON k.id = c.concept_id"
        " WHERE c.statement_id = %s AND c.carried",
        (sid,),
    ).fetchall()
    assert row == [("在水污染管制區內", "scope", True)]
    assert "適用範圍（皆須成立）：[ctx_" in render_annotation(conn, sid)


def test_the_model_cannot_make_a_condition_carried_by_writing_the_flag(
    conn: Conn, tmp_path: Path
) -> None:
    """承自上位是程式標的：模型自己寫 carried 的要件，不影響程式帶入，也不會被當成承自上位。"""
    answer = _item_answer()
    answer["statements"][0]["conditions"][0]["carried"] = True
    sid = _extract_item(conn, tmp_path, answer)

    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.statement_condition"
            " WHERE statement_id = %s AND carried",
            sid,
        )
        == 1
    )
