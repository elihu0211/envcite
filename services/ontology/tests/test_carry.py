"""上位的適用情形由程式帶入：款與半句話的引文不靠抽取者記得上位的條件。

案例取自義務重抽的抽查（`eval/duty-reextraction-2026-11-06.md`）：款漏掉導言的「在水污染管制區內」，
後半句漏掉句首的「第一項公告之檢驗結果未符合水體分類水質標準時」，引文截在並列的替代方式之前。
"""

from typing import Any

from envcite_ontology.verification.carry import (
    carry,
    concept_code,
    head_clauses,
    lead_in_clauses,
    truncated_issues,
)

LEAD_IN = "在水污染管制區內，不得有下列行為：\n"
PT_1 = "一、使用農藥或化學肥料，致有污染主管機關指定之水體之虞。"
ART_10_5 = (
    "第一項公告之檢驗結果未符合水體分類水質標準時，各目的事業主管機關應定期監測水體中食用植物、"
    "魚、蝦、貝類及底泥中重金屬、毒性化學物質及農藥含量，如有致危害人體健康、農漁業生產之虞時，"
    "並應採取禁止採捕食用水產動、植物之措施。"
)
ART_18_1_2 = (
    "前項廢（污）水須經處理始能符合本法所定管制標準者，不得於排放（入）前，"
    "與無需處理即能符合標準之水混合稀釋。"
)
ART_18_1_1 = (
    "事業或污水下水道系統產生之廢（污）水，應經核准登記之收集、處理單元、流程，並由核准登記之"
    "放流口排放，或依下水道管理機關（構）核准之排放口排入污水下水道，不得繞流排放。"
)


def _duty(text: str, quote: str, scope: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    assert quote in text
    return {
        "id": "s1",
        "quote": quote,
        "kind": "prescriptive",
        "deontic": "prohibition",
        "slots": {"subject": {"unstated": True}},
        "conditions": [
            *({"role": "scope", "clause": 1, "concept": c} for c in scope or []),
            {"role": "duty", "clause": 1, "concept": "duty"},
        ],
    } | extra


def _labels(*codes: str) -> dict[str, str]:
    return {c: c for c in codes} | {"duty": "使用農藥或化學肥料"}


def _carried(statement: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in statement["conditions"] if c.get("carried")]


def test_the_lead_in_conditions_are_the_clauses_before_the_modal_of_a_list_of_acts() -> None:
    assert lead_in_clauses(LEAD_IN) == ["在水污染管制區內"]
    # 「有下列情形之一」是款在當條件，款不是義務：導言的條件不往款帶
    assert lead_in_clauses("事業有下列情形之一者，得免再依前項規定經技師簽證：") == []
    assert lead_in_clauses("本法專用名詞定義如下：") == []


def test_an_item_without_the_lead_in_condition_gets_it_carried_in() -> None:
    quote = "使用農藥或化學肥料，致有污染主管機關指定之水體之虞。"
    out, concepts = carry([_duty(PT_1, quote)], PT_1, LEAD_IN, _labels())

    [added] = _carried(out[0])
    assert added == {
        "role": "scope",
        "clause": 1,
        "concept": concept_code("在水污染管制區內"),
        "comparator": "eq",
        "threshold": 1,
        "carried": True,
    }
    assert concepts == [{"code": concept_code("在水污染管制區內"), "label": "在水污染管制區內"}]


def test_a_condition_the_statement_already_wrote_is_not_carried_twice() -> None:
    quote = "使用農藥或化學肥料，致有污染主管機關指定之水體之虞。"
    out, concepts = carry(
        [_duty(PT_1, quote, scope=["水污染管制區內"])], PT_1, LEAD_IN, _labels("水污染管制區內")
    )

    assert _carried(out[0]) == [] and concepts == []


def test_the_latter_half_of_a_sentence_gets_the_condition_at_the_head_of_the_sentence() -> None:
    quote = "如有致危害人體健康、農漁業生產之虞時，並應採取禁止採捕食用水產動、植物之措施"
    assert head_clauses(ART_10_5, ART_10_5.index(quote), quote) == [
        "第一項公告之檢驗結果未符合水體分類水質標準時",
        "各目的事業主管機關",
    ]
    out, concepts = carry([_duty(ART_10_5, quote)], ART_10_5, "", _labels())

    # 句首的主體成為未明示的主體；條件成為承自上位的 scope
    assert out[0]["slots"]["subject"] == [{"literal": "各目的事業主管機關"}]
    assert [c["label"] for c in concepts] == ["第一項公告之檢驗結果未符合水體分類水質標準時"]


def test_a_quote_that_starts_at_the_modal_gets_the_whole_condition_before_it() -> None:
    quote = "不得於排放（入）前，與無需處理即能符合標準之水混合稀釋"
    out, concepts = carry([_duty(ART_18_1_2, quote)], ART_18_1_2, "", _labels())

    assert [c["label"] for c in concepts] == ["前項廢（污）水須經處理始能符合本法所定管制標準者"]
    assert len(_carried(out[0])) == 1


def test_a_quote_that_brings_its_own_subject_does_not_take_the_clause_before_it() -> None:
    text = "各級主管機關得派員檢查，公私場所之所有人、使用人或管理人，不得規避、妨礙或拒絕。"
    quote = "公私場所之所有人、使用人或管理人，不得規避、妨礙或拒絕"

    assert head_clauses(text, text.index(quote), quote) == []


def test_a_quote_at_the_start_of_its_sentence_has_nothing_to_carry() -> None:
    text = "前項建築物污水處理設施之建造、管理及清理，應符合規定。事業應申報。"
    out, concepts = carry([_duty(text, "事業應申報")], text, "", _labels())

    assert concepts == [] and _carried(out[0]) == []


def test_a_condition_is_carried_into_every_clause_of_a_scope_that_is_an_or() -> None:
    quote = "使用農藥或化學肥料，致有污染主管機關指定之水體之虞。"
    statement = _duty(PT_1, quote)
    statement["conditions"] = [
        {"role": "scope", "clause": 1, "concept": "a"},
        {"role": "scope", "clause": 2, "concept": "b"},
        {"role": "duty", "clause": 1, "concept": "duty"},
    ]
    out, _ = carry([statement], PT_1, LEAD_IN, _labels("a", "b"))

    assert sorted(c["clause"] for c in _carried(out[0])) == [1, 2]


def test_a_lead_in_that_names_an_actor_becomes_the_subject_of_an_item_without_one() -> None:
    lead = "各級主管機關得派員進入場所，為下列各項查證工作：\n"
    quote = "檢查污染物排放情形。"
    out, concepts = carry([_duty(f"一、{quote}", quote)], f"一、{quote}", lead, _labels())

    assert out[0]["slots"]["subject"] == [{"literal": "各級主管機關"}]
    assert concepts == []


def test_statements_that_are_not_duties_are_left_alone() -> None:
    sanction = {"id": "p", "quote": "處罰", "kind": "sanction"}
    out, concepts = carry([sanction], "處罰", LEAD_IN, {})

    assert out == [sanction] and concepts == []


def test_a_quote_cut_before_a_parallel_alternative_is_sent_back() -> None:
    quote = "應經核准登記之收集、處理單元、流程，並由核准登記之放流口排放"
    [issue] = truncated_issues([_duty(ART_18_1_1, quote)], ART_18_1_1)

    assert "或依下水道管理機關" in issue


def test_the_rest_of_a_sentence_another_statement_quotes_is_not_a_truncation() -> None:
    first = "應經核准登記之收集、處理單元、流程，並由核准登記之放流口排放"
    second = "不得繞流排放"
    rest = _duty(ART_18_1_1, "或依下水道管理機關（構）核准之排放口排入污水下水道，不得繞流排放")
    assert truncated_issues([_duty(ART_18_1_1, first), rest], ART_18_1_1) == []
    assert truncated_issues([_duty(ART_18_1_1, ART_18_1_1[:-1])], ART_18_1_1) == []
    assert second in ART_18_1_1
