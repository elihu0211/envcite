"""抽取器寫出環境管制的標註：責任基礎、委由個別處分、效果的觸發；形狀不對的部分只丟那一部分。"""

from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.annotate import load
from envcite_ontology.verification.extract import (
    STATEMENTS_FORMAT,
    extract_unit,
    shape_issues,
    tidy,
)
from envcite_ontology.verification.link import link_penalties
from tests.reasoning_support import _plain_duty
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

    assert out.loaded is not None and (out.loaded.statements, out.loaded.triggers) == (3, 1)
    assert out.dropped == ()
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

    assert out.loaded is not None and (out.loaded.statements, out.loaded.triggers) == (3, 0)
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


def test_linking_drops_a_penalty_whose_trigger_was_not_linked(conn: Conn, tmp_path: Path) -> None:
    """待連結的罰則以沒連上的罰則為前提：連帶丟掉，不讓整份文件因懸空的引用被拒。"""
    penalty_text = "違反第一條規定者，處新臺幣一萬元以上十萬元以下罰鍰"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", penalty_text)))
    load(conn, _doc(_plain_duty(text="排放廢水者應符合標準")))
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
    base = {k: v for k, v in _doc().items() if k != "statements"}

    linked = link_penalties(conn, FakeModel({}), [base | {"statements": [held]}])

    assert linked.statements == 0
    assert linked.dropped == ("a1: excepts or is triggered by a penalty that was not linked",)
