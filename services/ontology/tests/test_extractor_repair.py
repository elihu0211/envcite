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
    completeness_issues,
    extract_unit,
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


def test_a_penalty_that_punishes_a_held_penalty_is_held_with_it(conn: Conn, tmp_path: Path) -> None:
    """同一條原文裡的附加處罰（按次處罰、停工）常被模型標成處罰第一個罰則：一起留待連結。"""
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

    assert out.loaded is not None and out.loaded.statements == 1
    assert out.dropped == ()
    assert [s["id"] for d in out.pending for s in d["statements"]] == ["p1", "p2"]


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

    assert out.loaded is None and out.pending == ()


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
