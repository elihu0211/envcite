"""抽取器寫出環境管制的標註：責任基礎、委由個別處分、效果的觸發；形狀不對的部分只丟那一部分。"""

from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.annotate import AnnotationError, load
from envcite_ontology.verification.extract import (
    STATEMENTS_FORMAT,
    extract_unit,
    shape_issues,
    tidy,
)
from envcite_ontology.verification.link import link_penalties
from tests.test_annotation import LAW, _doc
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import Conn, FakeModel

TEXT = "排放廢水者應符合許可量，違反者限期改善，屆期未改善者按次處罰"


def _answer() -> dict[str, Any]:
    return {
        "concepts": [
            {"code": "discharging", "label": "排放廢水"},
            {"code": "emission", "label": "排放量"},
            {"code": "operating_permit", "label": "操作許可證"},
            {"code": "lapsed", "label": "屆期未改善"},
        ],
        "statements": [
            {
                "id": "s1",
                "quote": "排放廢水者應符合許可量",
                "kind": "prescriptive",
                "deontic": "obligation",
                "slots": {
                    "subject": {"literal": "排放廢水者", "liability": "state"},
                    "action": {"literal": "排放", "liability": "act"},
                },
                "conditions": [
                    {
                        "role": "scope",
                        "clause": 1,
                        "concept": "discharging",
                        "comparator": "eq",
                        "threshold": 1,
                    },
                    {
                        "role": "duty",
                        "clause": 1,
                        "concept": "emission",
                        "comparator": "le",
                        "threshold": 0,
                        "permit": "operating_permit",
                    },
                ],
            },
            {
                "id": "o1",
                "quote": "違反者限期改善",
                "kind": "sanction",
                "sanction": {"effect": "order", "punishes": "s1"},
            },
            {
                "id": "a1",
                "quote": "屆期未改善者按次處罰",
                "kind": "sanction",
                "conditions": [
                    {
                        "role": "scope",
                        "clause": 1,
                        "concept": "lapsed",
                        "comparator": "eq",
                        "threshold": 1,
                    }
                ],
                "sanction": {
                    "effect": "fine",
                    "repeating": True,
                    "punishes": "s1",
                    "triggers": [{"statement": "o1", "kind": "order_lapsed"}],
                },
            },
        ],
    }


def test_the_extractor_writes_liability_permits_and_triggers(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT)))

    out = extract_unit(
        conn, FakeModel(_answer()), LAW, "art_1", agent="fake-model", agent_version="1"
    )

    # 義務還沒確認：兩個罰則存成待連結罰則，義務確認後才寫成陳述
    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 2)
    assert out.dropped == ()
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = %s",
        (out.loaded.ids["s1"],),
    )
    assert link_penalties(conn, None).statements == 2
    assert conn.execute(
        "SELECT slot_role::text, liability_basis::text FROM annotation.statement_slot"
        " ORDER BY slot_role"
    ).fetchall() == [("action", None), ("subject", "state")]
    assert conn.execute(
        "SELECT c.threshold, k.code FROM annotation.statement_condition AS c"
        "  JOIN vocab.concept AS k ON k.id = c.delegate_permit_concept_id"
    ).fetchall() == [(None, "operating_permit")]
    assert _scalar(conn, "SELECT trigger_kind::text FROM annotation.sanction_trigger") == (
        "order_lapsed"
    )


@pytest.mark.parametrize(
    ("trigger", "reason"),
    [
        (
            {"statement": "elsewhere", "kind": "order_lapsed"},
            "trigger elsewhere is not in this unit",
        ),
        ({"statement": "o1", "kind": "made_up"}, "a trigger is malformed"),
        ({"statement": "f1", "kind": "order_lapsed"}, "trigger f1 is not an order"),
    ],
)
def test_a_penalty_with_a_bad_trigger_is_dropped_whole(
    conn: Conn, tmp_path: Path, trigger: dict[str, str], reason: str
) -> None:
    """少了前提的罰則會被當成無條件，所以觸發不對就丟整個罰則，其餘照常寫入。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT)))
    answer = _answer()
    answer["statements"].append(
        {
            "id": "f1",
            "quote": "違反者限期改善",
            "kind": "sanction",
            "sanction": {"effect": "fine", "punishes": "s1"},
        }
    )
    answer["statements"][2]["sanction"]["triggers"] = [trigger]

    out = extract_unit(conn, FakeModel(answer), LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 2)
    assert out.dropped == (f"a1: {reason}",)


def test_two_threshold_sources_are_a_shape_problem() -> None:
    answer = _answer()
    answer["statements"][0]["conditions"][1]["value_concept"] = "emission"

    assert any("只能有一種來源" in p for p in shape_issues(tidy(answer))["s1"])


def test_an_undeclared_permit_kind_is_a_shape_problem() -> None:
    answer = _answer()
    answer["concepts"] = [c for c in answer["concepts"] if c["code"] != "operating_permit"]

    assert any("permit「operating_permit」" in p for p in shape_issues(tidy(answer))["s1"])


def test_the_decoding_schema_knows_the_new_shapes() -> None:
    statement = STATEMENTS_FORMAT["json_schema"]["schema"]["properties"]["statements"]["items"]
    sanction = statement["properties"]["sanction"]["anyOf"][0]["properties"]
    assert "disgorgement" in sanction["effect"]["enum"]
    assert "training" not in sanction["effect"]["enum"]  # 講習是警告性處分，用 warning
    assert "triggers" in sanction
    condition = statement["properties"]["conditions"]["items"]["properties"]
    assert "permit" in condition


def test_a_held_penalty_triggered_by_a_statement_not_in_the_document_is_refused(
    conn: Conn, tmp_path: Path
) -> None:
    """待連結罰則的前提要在同一份文件裡：懸空的前提在載入時就拒絕，不留到連結才發現。"""
    penalty_text = "違反第一條規定者，處新臺幣一萬元以上十萬元以下罰鍰"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", penalty_text)))
    held = {
        "id": "a1",
        "law": LAW,
        "path": "art_2",
        "text": penalty_text,
        "kind": "sanction",
        "sanction": {
            "effect": "fine",
            "min": 10000,
            "max": 100000,
            "triggers": [{"statement": "o1", "kind": "order_lapsed"}],
        },
    }

    with pytest.raises(AnnotationError, match="引用了不存在的陳述：o1"):
        load(conn, {**_doc(), "statements": [held]})
    assert _scalar(conn, "SELECT count(*) FROM annotation.held_penalty") == 0


def test_a_quote_in_variant_characters_is_anchored_on_the_original_text(
    conn: Conn, tmp_path: Path
) -> None:
    """模型把原文的「污」寫成「汙」：仍定位得到，span 與錨定的文字取自原文。"""
    text = "排放廢（污）水者，應符合放流水標準"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(text)))
    answer = {
        "concepts": [
            {"code": "discharging", "label": "排放廢水"},
            {"code": "standard", "label": "放流水標準"},
        ],
        "statements": [
            {
                "id": "s1",
                "quote": "排放廢（汙）水者，應符合放流水標準",
                "kind": "prescriptive",
                "deontic": "obligation",
                "conditions": [
                    {
                        "role": "scope",
                        "clause": 1,
                        "concept": "discharging",
                        "comparator": "eq",
                        "threshold": 1,
                    },
                    {
                        "role": "duty",
                        "clause": 1,
                        "concept": "standard",
                        "comparator": "eq",
                        "threshold": 1,
                    },
                ],
            }
        ],
    }

    out = extract_unit(conn, FakeModel(answer), LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.dropped == () and out.loaded is not None and out.loaded.statements == 1
    assert conn.execute("SELECT span FROM annotation.statement").fetchall() == [(None,)]


def test_the_extractor_writes_a_limit_with_its_measurement(conn: Conn, tmp_path: Path) -> None:
    """限值帶檢測方法與平均時段；認定方式寫在限值以外、或限值標需判斷，去掉該欄而不丟整份。"""
    text = "戴奧辛排放標準值為○．一ng-TEQ/Nm3，以五次採樣中間三數值之算術平均認定"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(text)))
    answer = {
        "concepts": [
            {"code": "dioxin", "label": "戴奧辛"},
            {"code": "stack_sampling", "label": "人工採樣檢測"},
            {"code": "middle_three_mean", "label": "中間三數值之算術平均"},
            {"code": "standard", "label": "排放標準"},
        ],
        "statements": [
            {
                "id": "lim",
                "quote": text,
                "kind": "constitutive",
                "conditions": [
                    {
                        "role": "limit",
                        "clause": 1,
                        "concept": "dioxin",
                        "comparator": "le",
                        "threshold": 0.1,
                        "unit": "ng-TEQ/Nm3",
                        "judgement": True,
                        "method": "stack_sampling",
                        "averaging": "middle_three_mean",
                    },
                    {
                        "role": "scope",
                        "clause": 1,
                        "concept": "standard",
                        "comparator": "eq",
                        "threshold": 1,
                        "averaging": "middle_three_mean",
                    },
                ],
            }
        ],
    }

    out = extract_unit(conn, FakeModel(answer), LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.dropped == () and out.loaded is not None and out.loaded.statements == 1
    assert conn.execute(
        "SELECT c.condition_role::text, c.needs_assessment, m.code, a.code"
        "  FROM annotation.statement_condition AS c"
        "  LEFT JOIN vocab.concept AS m ON m.id = c.measurement_method_concept_id"
        "  LEFT JOIN vocab.concept AS a ON a.id = c.averaging_period_concept_id ORDER BY c.id"
    ).fetchall() == [
        ("limit", False, "stack_sampling", "middle_three_mean"),
        ("scope", False, None, None),
    ]


def test_the_extraction_schema_offers_limits_and_their_measurement() -> None:
    condition = STATEMENTS_FORMAT["json_schema"]["schema"]["properties"]["statements"]["items"][
        "properties"
    ]["conditions"]["items"]["properties"]
    assert "limit" in condition["role"]["enum"]
    assert {"method", "averaging"} <= set(condition)


def test_a_limit_without_a_threshold_is_a_shape_issue() -> None:
    answer = {
        "concepts": [{"code": "dioxin", "label": "戴奧辛"}],
        "statements": [
            {
                "id": "lim",
                "quote": "x",
                "kind": "constitutive",
                "conditions": [
                    {"role": "limit", "clause": 1, "concept": "dioxin", "comparator": "le"}
                ],
            }
        ],
    }
    assert "lim" in shape_issues(answer)
