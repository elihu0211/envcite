"""Definitions are extracted only under a「本法用語，定義如下」clause, into the law's own
scheme, and the statements extracted afterwards may name the defined terms — the codes agree from
the start."""

import json
from pathlib import Path
from typing import Any

from envcite_ontology.verification.definitions import (
    defined_terms,
    definition_paths,
    extract_definitions,
)
from envcite_ontology.verification.extract import extract_unit
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import Conn, FakeModel

CHAPEAU = "本法用語，定義如下："
ITEM_1 = "一、事業：指工廠、礦場。"
ITEM_2 = "二、廢水：指事業排放之水。"
ARTICLE_2 = f"{CHAPEAU}\r\n{ITEM_1}\r\n{ITEM_2}"
PATH_1 = "art_2/para_1/pt_1"
PATH_2 = "art_2/para_1/pt_2"


def _law(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", ARTICLE_2)))


def _answer(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "definitions": [
            {
                "quote": ITEM_1,
                "term": "事業",
                "code": "business",
                "members": [
                    {"label": "工廠", "code": "factory"},
                    {"label": "礦場", "code": "mine"},
                ],
            }
        ]
    }
    return base | over


def test_only_the_items_under_a_definition_clause_are_definition_units(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)

    assert definition_paths(conn, LAW) == [PATH_1, PATH_2]


def test_a_definition_is_loaded_into_the_laws_own_scheme_as_extracted(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)

    out = extract_definitions(
        conn, FakeModel(_answer()), LAW, PATH_1, agent="fake-model", agent_version="1"
    )

    assert out.loaded is not None and out.loaded.definitions == 1
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM vocab.concept_scheme"
            " WHERE code = %s AND scope_resource_id IS NOT NULL",
            f"def.{LAW}",
        )
        == 1
    )
    assert _scalar(conn, "SELECT confirm_state::text FROM annotation.claim") == ("extracted")
    assert _scalar(conn, "SELECT count(*) FROM annotation.term_definition_member") == 2
    assert defined_terms(conn, LAW) == {"business": "事業"}


def test_a_member_the_text_does_not_have_is_sent_back_and_then_dropped(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)
    invented = _answer()
    invented["definitions"][0]["members"].append({"label": "學校", "code": "school"})
    model = FakeModel(invented)

    out = extract_definitions(conn, model, LAW, PATH_1, agent="fake-model", agent_version="1")

    assert out.loaded is None
    assert len(model.prompts) == 3  # 原提示加兩輪修復
    assert "學校" in model.prompts[1]
    assert _scalar(conn, "SELECT count(*) FROM annotation.term_definition") == 0


def test_a_definition_without_an_enumerated_member_is_not_a_usable_definition(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)
    bare = {
        "definitions": [
            {"quote": ITEM_2, "term": "廢水", "code": "wastewater", "members": []},
        ]
    }

    out = extract_definitions(
        conn, FakeModel(bare), LAW, PATH_2, agent="fake-model", agent_version="1"
    )

    assert out.loaded is None
    assert any("沒有明文列舉的成員" in reason for reason in out.dropped)


def _statement_answer(value_concept: str) -> dict[str, Any]:
    return {
        "concepts": [
            {"code": "discharging", "label": "排放廢水"},
            {"code": "actor_type", "label": "行為人的類別"},
        ],
        "statements": [
            {
                "id": "s1",
                "quote": "排放廢水者應符合標準",
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
                        "concept": "actor_type",
                        "comparator": "eq",
                        "threshold": 1,
                        "value_concept": value_concept,
                    },
                ],
            }
        ],
    }


def test_a_statement_may_name_a_defined_term_and_the_codes_agree(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)
    extract_definitions(conn, FakeModel(_answer()), LAW, PATH_1, agent="m", agent_version="1")
    terms = defined_terms(conn, LAW)
    model = FakeModel(_statement_answer("business"))

    out = extract_unit(
        conn,
        model,
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
        defined=terms,
    )

    assert "- business：事業" in model.prompts[0]  # 用語清單進了提示
    assert out.loaded is not None and out.loaded.statements == 1
    # 與概念比較的要件沒有門檻值（模型多寫的 threshold 被去掉）；比較的是用語的代碼。
    assert (
        _scalar(
            conn,
            "SELECT k.code FROM annotation.statement_condition AS c"
            "  JOIN vocab.concept AS k ON k.id = c.value_concept_id",
        )
        == "business"
    )
    assert (
        _scalar(
            conn,
            "SELECT threshold FROM annotation.statement_condition"
            " WHERE value_concept_id IS NOT NULL",
        )
        is None
    )


def test_a_statement_cannot_name_a_term_the_law_did_not_define(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    extract_definitions(conn, FakeModel(_answer()), LAW, PATH_1, agent="m", agent_version="1")
    model = FakeModel(_statement_answer("school"))

    out = extract_unit(
        conn,
        model,
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
        defined=defined_terms(conn, LAW),
    )

    assert out.loaded is None  # 修不好就丟，不連累別的單元
    assert any("不在本法已定義的用語清單" in p for p in model.prompts[1:])


def test_without_defined_terms_the_prompt_is_unchanged(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    model = FakeModel(json.dumps({"concepts": [], "statements": []}))

    extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert "本法已定義的用語" not in model.prompts[0]


class _ByPrompt:
    """答案依提示是定義項還是一般條文而定。"""

    name = "fake:model"
    family = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "之下的一款原文" in prompt:
            if ITEM_2 in prompt.split("原文（")[-1]:
                bare = {
                    "definitions": [{"quote": ITEM_2, "term": "廢水", "code": "w", "members": []}]
                }
                return json.dumps(bare, ensure_ascii=False)
            return json.dumps(_answer(), ensure_ascii=False)
        return json.dumps(_statement_answer("business"), ensure_ascii=False)


def test_the_pipeline_extracts_definitions_first_and_does_not_treat_them_as_norms(
    conn: Conn, tmp_path: Path
) -> None:
    from envcite_ontology.verification.run import run

    _law(conn, tmp_path)
    model = _ByPrompt()

    result = run(conn, model, [], [LAW], agent_version="1", required=["numbers"], gates=())

    definition_calls = [c for c in model.calls if "之下的一款原文" in c]
    statement_calls = [c for c in model.calls if "之下的一款原文" not in c]
    assert result.definitions == 1  # 第二款沒有列舉成員，丟掉的不算
    assert len(definition_calls) == 2  # 兩個定義項各一次
    assert len(statement_calls) >= 1
    assert all("- business：事業" in c for c in statement_calls)  # 陳述抽取讀得到用語清單
    # 定義項不當規範抽取：沒有任何陳述錨在定義項上。
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.statement AS s"
            "  JOIN corpus.provision AS p ON p.id = s.provision_id WHERE p.path LIKE 'art_2/%%'",
        )
        == 0
    )
