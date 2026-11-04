"""Completeness check and repair: an incomplete answer is sent back with the problem and the
fix is used."""

import json
from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.verification import (
    NumbersGate,
    verify,
)
from envcite_ontology.verification.extract import (
    MAX_REPAIRS,
    STATEMENTS_FORMAT,
    borrowed_issues,
    completeness_issues,
    criminal_penalties,
    extract_unit,
    fragment_issues,
    fragments,
    sanction_issues,
    uncovered_issues,
)
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    EXTRACTED,
    Conn,
    FakeModel,
    Sequence_,
    _duty_and_cited_penalty,
    _water_with,
)


def _duty_without_scope() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [doc["statements"][0]]
    return doc


def test_a_duty_without_a_duty_condition_is_incomplete() -> None:
    statement = {"id": "s1", "kind": "prescriptive", "deontic": "obligation", "quote": "應符合標準"}

    issues = completeness_issues([{**statement, "conditions": [{"role": "scope"}]}])

    assert len(issues) == 1 and "duty" in issues[0]
    assert completeness_issues([{**statement, "conditions": [{"role": "duty"}]}]) == []


def test_the_conditional_pattern_needs_a_scope_condition() -> None:
    statement = {
        "id": "s1",
        "kind": "prescriptive",
        "deontic": "obligation",
        "quote": "排放廢水者，應符合標準",
        "conditions": [{"role": "duty"}],
    }

    issues = completeness_issues([statement])

    assert len(issues) == 1 and "scope" in issues[0]
    complete = {**statement, "conditions": [{"role": "scope"}, {"role": "duty"}]}
    assert completeness_issues([complete]) == []


def test_permissions_and_other_kinds_need_no_duty() -> None:
    assert completeness_issues([{"id": "a", "kind": "prescriptive", "deontic": "permission"}]) == []
    assert completeness_issues([{"id": "b", "kind": "constitutive", "quote": "甲者，應乙"}]) == []


def _without_duty() -> dict[str, Any]:
    doc = _duty_without_scope()
    doc["statements"][0]["conditions"] = [doc["statements"][0]["conditions"][0]]  # 只剩 scope
    return doc


def test_an_incomplete_answer_is_sent_back_with_the_problem_and_the_fix_is_used(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = Sequence_(_without_duty(), _duty_without_scope())

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert len(model.prompts) == 2 and "沒有 duty 要件" in model.prompts[1]
    assert out.loaded is not None
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.statement_condition WHERE condition_role = 'duty'",
        )
        == 1
    )


def test_repair_stops_after_the_allowed_rounds(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = Sequence_(_without_duty())

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert len(model.prompts) == 1 + MAX_REPAIRS
    assert out.loaded is None  # 修不好的、沒有 duty 的義務不寫入
    assert out.dropped == ("s1: obligation or prohibition without a duty condition after repair",)


def test_a_penalty_that_punishes_a_held_penalty_is_held_too(conn: Conn, tmp_path: Path) -> None:
    """同一條原文裡的附加處罰（按次處罰、停工）常被模型標成處罰第一個罰則：罰則只處罰義務，
    所以它也不指明受罰者，和第一個罰則一樣經被引條文連結。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = _duty_and_cited_penalty()
    doc["statements"].append(
        {
            "id": "p2",
            "quote": "排放廢水者應符合標準",
            "kind": "sanction",
            "sanction": {"effect": "order", "punishes": "p1"},
        }
    )

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is not None and (out.loaded.statements, out.loaded.held) == (1, 2)
    assert out.dropped == ()
    assert conn.execute(
        "SELECT count(*) FROM annotation.held_penalty WHERE punished_statement_id IS NULL"
    ).fetchone() == (2,)


def test_numbers_gate_evidence_can_be_stored_when_the_model_invented_an_amount(
    conn: Conn, sample: None
) -> None:
    """真實模型給一個原文沒有金額的罰則填了額度：閘門判失敗，證據要能寫成 JSON。"""
    ids = _water_with(conn, "p40_1_order", min=100, max=2000).ids

    result = NumbersGate().check(conn, ids["p40_1_order"])

    assert result is not None and result.verdict == "fail"
    json.dumps(result.evidence)  # 不得含 Decimal
    verify(conn, [NumbersGate()])  # 寫進 annotation.verification 也不得崩潰


def test_a_prohibition_without_a_duty_is_not_written_even_though_it_could_pass_the_model_gates(
    conn: Conn, tmp_path: Path
) -> None:
    """禁止沒有 duty 時可執行閘門無法翻轉結論，所以不寫入，避免只靠模型閘門就被確認。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = _without_duty()
    doc["statements"][0]["deontic"] = "prohibition"

    out = extract_unit(
        conn,
        Sequence_(doc),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is None
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda s: s.update(conditions=["符合標準"]), id="condition-is-a-string"),
        pytest.param(lambda s: s.update(conditions="符合標準"), id="conditions-not-a-list"),
        pytest.param(lambda s: s.update(slots=["事業"]), id="slots-not-an-object"),
    ],
)
def test_a_statement_with_the_wrong_shape_is_dropped_not_fatal(
    conn: Conn, tmp_path: Path, damage: Any
) -> None:
    """真實模型把物件寫成字串過：原本整條管線因 AttributeError 中斷，現在丟掉整份並記下。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    doc = json.loads(json.dumps(EXTRACTED))
    damage(doc["statements"][0])

    out = extract_unit(
        conn,
        FakeModel(json.dumps(doc, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is None
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


@pytest.mark.parametrize(
    "statements",
    [
        pytest.param("一個字串", id="a-string"),
        pytest.param(["字串", 3, None], id="items-not-objects"),
        pytest.param({"id": "s1"}, id="an-object"),
    ],
)
def test_statements_that_are_not_a_list_of_objects_count_as_none(
    conn: Conn, tmp_path: Path, statements: Any
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))

    out = extract_unit(
        conn,
        FakeModel(json.dumps({"concepts": [], "statements": statements}, ensure_ascii=False)),
        LAW,
        "art_1",
        agent="fake-model",
        agent_version="1",
    )

    assert out.loaded is None
    assert _scalar(conn, "SELECT count(*) FROM annotation.held_penalty") == 0


def test_counts_record_the_calls_and_repairs_of_a_unit(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = Sequence_(_without_duty(), _duty_without_scope())

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.counts.calls == 2 and out.counts.repairs == 1
    assert out.counts.shape_failures == 0  # 缺 duty 是完備性問題，不是形狀問題


def test_counts_stop_at_the_repair_cap(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = Sequence_(_without_duty())

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.counts.calls == 1 + MAX_REPAIRS and out.counts.repairs == MAX_REPAIRS


def test_an_unparseable_first_answer_counts_as_a_syntax_failure(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))

    out = extract_unit(
        conn, FakeModel("不是 JSON"), LAW, "art_1", agent="fake-model", agent_version="1"
    )

    assert out.loaded is None
    assert out.counts.syntax_failures == 1 and out.counts.shape_failures == 0
    assert out.counts.calls == 1 and out.counts.repairs == 0


class _ConstrainedModel(FakeModel):
    constrained_extraction = True

    def __init__(self, answer: Any) -> None:
        super().__init__(answer)
        self.formats: list[Any] = []

    def complete(self, prompt: str, *, response_format: Any = None) -> str:
        self.formats.append(response_format)
        return super().complete(prompt)


def test_a_constrained_model_gets_the_statements_schema_on_every_call(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = _ConstrainedModel(_without_duty())

    extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert model.formats == [STATEMENTS_FORMAT] * (1 + MAX_REPAIRS)


def test_an_unconstrained_model_is_called_exactly_as_before(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    model = FakeModel(_duty_without_scope())

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is not None and len(model.prompts) == 1


def test_a_duty_that_borrows_a_defined_term_is_sent_back_unless_it_is_a_value_concept() -> None:
    defined = {"measures": "水污染防治措施", "business": "事業"}
    statement = {
        "id": "s1",
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            {"role": "scope", "concept": "business", "value_concept": "business"},
            {"role": "duty", "concept": "measures"},
        ],
    }

    issues = borrowed_issues([statement], defined)

    assert len(issues) == 1 and "measures" in issues[0] and "duty" in issues[0]
    own = {**statement, "conditions": [{"role": "duty", "concept": "sludge_handled"}]}
    assert borrowed_issues([own], defined) == []
    assert borrowed_issues([statement], None) == []


def _penalty(ident: str, quote: str, effect: str = "fine", **extra: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "kind": "sanction",
        "quote": quote,
        "sanction": {"effect": effect, **extra},
    }


def test_a_penalty_whose_quote_holds_another_effect_is_sent_back() -> None:
    joined = _penalty(
        "s1", "處新臺幣一萬元以上十萬元以下罰鍰，並通知限期補正，屆期仍未補正者，按次處罰"
    )

    issues = sanction_issues([joined])

    assert len(issues) == 1 and "s1" in issues[0]
    assert "order" in issues[0] and "repeating" in issues[0]


def test_penalties_that_each_quote_their_own_clause_are_complete() -> None:
    split = [
        _penalty("s1", "處新臺幣一萬元以上十萬元以下罰鍰"),
        _penalty("s2", "通知限期補正", "order"),
        _penalty("s3", "屆期仍未補正者，按次處罰", repeating=True),
        _penalty("s4", "情節重大者，禁止供飲用", "restriction"),
    ]

    assert sanction_issues(split) == []


def test_a_repeating_fine_may_quote_its_own_repeating_clause_but_not_the_order() -> None:
    assert sanction_issues([_penalty("s1", "按日連續處罰", repeating=True)]) == []
    assert sanction_issues([_penalty("s1", "按日連續處罰", repeating=False)]) != []


def test_penalties_of_other_kinds_are_not_checked_for_split_effects() -> None:
    other = {"id": "c1", "kind": "constitutive", "quote": "罰鍰並通知限期改善"}

    assert sanction_issues([other]) == []


def test_a_criminal_fine_is_not_an_administrative_fine() -> None:
    own = _penalty("s1", "違反第三條規定者，處新臺幣六萬元以下罰金", amount_text="六萬元以下罰金")
    item = _penalty("s2", "一、違反第五條第一項規定。", amount_text="新臺幣六萬元以下")
    parent = "有下列情形之一者，處一年以下有期徒刑、拘役，得併科新臺幣六萬元以下罰金："

    assert set(criminal_penalties([own], own["quote"], "")) == {"s1"}
    assert set(criminal_penalties([item], item["quote"], parent)) == {"s2"}


def test_an_administrative_fine_is_not_mistaken_for_a_criminal_one() -> None:
    fine = _penalty("s1", "一、未依規定申報。", amount_text="新臺幣一萬元以上十萬元以下")
    parent = "有下列情形之一者，處新臺幣一萬元以上十萬元以下罰鍰："
    mixed = "處罰鍰；其情節重大者，處有期徒刑"

    assert criminal_penalties([fine], fine["quote"], parent) == {}
    assert criminal_penalties([fine], mixed, "") == {}


def test_a_criminal_penalty_is_sent_back_and_dropped_when_it_stays(
    conn: Conn, tmp_path: Path
) -> None:
    doc: dict[str, Any] = {
        "concepts": [],
        "statements": [
            {
                "id": "p1",
                "quote": "處一年以下有期徒刑，得併科新臺幣六萬元以下罰金",
                "kind": "sanction",
                "sanction": {"effect": "fine", "amount_text": "新臺幣六萬元以下罰金"},
            }
        ],
    }
    _first(
        conn,
        tmp_path,
        _law_v1(
            LawArticles=_articles(
                "違反本法者，處一年以下有期徒刑，得併科新臺幣六萬元以下罰金", "乙一"
            )
        ),
    )
    model = FakeModel(doc)

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is None
    assert any("criminal penalty" in reason for reason in out.dropped)
    assert len(model.prompts) == 1 + MAX_REPAIRS
    assert "刑罰" in model.prompts[-1]


def _duty(ident: str, quote: str) -> dict[str, Any]:
    return {"id": ident, "kind": "prescriptive", "deontic": "obligation", "quote": quote}


def test_a_duty_quoting_a_clause_without_its_modal_is_a_fragment() -> None:
    text = "其有疏漏致污染水體者，應立即採取緊急應變措施，並於事故發生後三小時內，通知當地主管機關"
    whole = _duty("s1", text)
    clause = _duty("s2", "並於事故發生後三小時內，通知當地主管機關")

    assert fragments([whole, clause], text) == {"s2": clause["quote"]}
    assert "s2" in fragment_issues([clause], text)[0]


def test_an_item_whose_modal_is_in_the_parent_clause_is_not_a_fragment() -> None:
    item = _duty("s1", "在水體或其沿岸規定距離內棄置垃圾")

    assert fragments([item], "二、在水體或其沿岸規定距離內棄置垃圾。") == {}


def test_only_prescriptive_statements_are_checked_for_fragments() -> None:
    penalty = {"id": "p1", "kind": "sanction", "quote": "處新臺幣一萬元以上罰鍰"}

    assert fragments([penalty], "違反前條規定者，處新臺幣一萬元以上罰鍰") == {}


def test_a_fragment_is_sent_back_and_dropped_when_it_stays(conn: Conn, tmp_path: Path) -> None:
    text = "事業應採行防治措施，並保存紀錄"
    doc: dict[str, Any] = {
        "concepts": [{"code": "records_kept", "label": "紀錄已保存"}],
        "statements": [
            {
                "id": "s1",
                "quote": "並保存紀錄",
                "kind": "prescriptive",
                "deontic": "obligation",
                "conditions": [{"role": "duty", "concept": "records_kept", "threshold": 1}],
            }
        ],
    }
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(text, "乙一")))
    model = FakeModel(doc)

    out = extract_unit(conn, model, LAW, "art_1", agent="fake-model", agent_version="1")

    assert out.loaded is None
    assert any("without a modal" in reason for reason in out.dropped)
    assert len(model.prompts) == 1 + MAX_REPAIRS
    assert "規則 14" in model.prompts[-1]


def _reported(quote: str, labels: dict[str, str], subject: str = "事業") -> list[str]:
    roles = {"a": "scope", "b": "duty", "c": "duty"}
    statement = {
        **_duty("s1", quote),
        "slots": {"subject": [{"literal": subject}]},
        "conditions": [{"role": roles[code], "concept": code} for code in labels],
    }
    return uncovered_issues([statement], labels)


def test_a_duty_that_leaves_out_the_deadline_and_the_addressee_is_sent_back() -> None:
    quote = "其有疏漏致污染水體者，應立即採取緊急應變措施，並於事故發生後三小時內，通知當地主管機關"

    issues = _reported(quote, {"a": "有疏漏致污染水體", "b": "緊急應變措施", "c": "通知義務"})

    assert len(issues) == 1 and "duty" in issues[0]
    assert "三小時內" in issues[0] and "當地主管機關" in issues[0]


def test_a_duty_whose_conditions_cover_the_quote_is_complete() -> None:
    quote = "其有疏漏致污染水體者，應立即採取緊急應變措施，並於事故發生後三小時內，通知當地主管機關"
    labels = {
        "a": "有疏漏致污染水體",
        "b": "立即採取緊急應變措施",
        "c": "於事故發生後三小時內通知當地主管機關",
    }

    assert _reported(quote, labels) == []


def test_a_qualifier_of_the_subject_left_out_of_the_scope_is_sent_back() -> None:
    quote = "經中央主管機關指定公告之飲用水設備，應符合國家標準"

    issues = _reported(quote, {"a": "飲用水設備", "b": "符合國家標準"}, subject="飲用水設備")

    assert len(issues) == 1 and "scope" in issues[0] and "指定公告" in issues[0]
