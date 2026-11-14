"""環境管制的標註：責任基礎、委由個別處分、認定方式、效果的觸發、新增的效果種類與外部對映。

每一項都有生產者（載入器）與消費者（推論、`ListStatements`），這裡兩端一起測。
"""

from pathlib import Path
from typing import Any

import pytest

from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology.annotate import (
    LEGAL_EFFECTS,
    LIABILITY_BASES,
    TRIGGER_KINDS,
    AnnotationError,
    load,
)
from envcite_ontology.annotation_read import list_statements
from envcite_ontology.reasoning import evaluate
from envcite_ontology.reasoning.facts.case_inputs import Measurement
from envcite_ontology.reasoning_codec import enums
from envcite_ontology.verification.gates import ExecutableGate
from envcite_ontology.verification.model_gates import render_annotation
from tests.reasoning_support import Conn, _plain_duty, _scope, _with
from tests.test_annotation import BEFORE, LAW, _doc, _duty, _limit
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_id, _law_v1
from tests.verification_support import _with_penalties


def _law(conn: Conn, tmp_path: Path, n: int = 4) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(*(f"條{i}" for i in range(1, n + 1)))))


def _statement(ident: str, path: str, text: str, **body: Any) -> dict[str, Any]:
    return {"id": ident, "law": LAW, "path": path, "text": text, **body}


# 責任基礎


def _remediation(liability: str | None, slot: str = "subject") -> dict[str, Any]:
    value: dict[str, Any] = {"literal": "污染土地關係人"}
    if liability is not None:
        value["liability"] = liability
    return _statement(
        "remediate",
        "art_1",
        "條1",
        kind="prescriptive",
        deontic="obligation",
        slots={slot: value},
        conditions=[_scope("discharging")],
    )


def test_the_liability_basis_is_kept_on_the_subject_and_listed(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _doc(_remediation("state")))

    listed = list_statements(conn, _law_id(conn), BEFORE)
    (slot,) = listed.statements[0].slots
    assert (slot.literal, slot.liability_basis) == ("污染土地關係人", pb.LIABILITY_BASIS_STATE)


def test_a_slot_without_a_liability_basis_has_none(conn: Conn, tmp_path: Path) -> None:
    """沒有標註是未知，不是「行為」。"""
    _law(conn, tmp_path)
    load(conn, _doc(_remediation(None)))

    (slot,) = list_statements(conn, _law_id(conn), BEFORE).statements[0].slots
    assert not slot.HasField("liability_basis")


def test_only_the_subject_has_a_liability_basis(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    with pytest.raises(AnnotationError, match="liability_basis_is_subject"):
        load(conn, _doc(_remediation("act", slot="object")))


def test_statements_differing_only_in_liability_basis_are_two_assertions(
    conn: Conn, tmp_path: Path
) -> None:
    """指紋含責任基礎：同一份內容載兩次沿用既有的，責任基礎不同就是另一個主張。"""
    _law(conn, tmp_path)
    load(conn, _doc(_remediation("act")))

    assert load(conn, _doc(_remediation("act"))).duplicates == 1
    assert load(conn, _doc(_remediation("state"))).duplicates == 0


# 委由個別處分


def _permitted(**over: Any) -> dict[str, Any]:
    """依許可證所載的排放量排放：門檻值是許可的量值，不在條文裡。"""
    duty = _plain_duty(text="條1")
    duty["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": "cod",
        "comparator": "le",
        "permit": "operating_permit",
        **over,
    }
    return duty


def test_a_condition_has_exactly_one_threshold_source(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    with pytest.raises(AnnotationError, match="one_threshold_source"):
        load(conn, _with(_doc(_permitted(threshold=100)), "operating_permit"))


def test_a_permit_threshold_is_listed_as_the_permit_kind(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _with(_doc(_permitted()), "operating_permit"))

    duty = list_statements(conn, _law_id(conn), BEFORE).statements[0].duty.any_of[0].all_of[0]
    assert (duty.WhichOneof("threshold"), duty.delegate_permit) == (
        "delegate_permit",
        "operating_permit",
    )


def test_a_permit_threshold_comes_from_the_caller(conn: Conn, tmp_path: Path) -> None:
    """許可量值由呼叫端提供；沒提供則未知並列出缺的那筆，兩個值則矛盾。"""
    _law(conn, tmp_path)
    duty = load(conn, _with(_doc(_permitted()), "operating_permit")).ids["duty"]
    case = {"discharging": 1.0, "cod": 150.0}

    unknown = evaluate(conn, BEFORE, case)
    assert unknown.verdicts[duty] == "undetermined"
    assert unknown.missing_permits == ((duty, "operating_permit", "cod"),)
    assert unknown.missing == ()

    over = evaluate(conn, BEFORE, case, permits={("operating_permit", "cod"): [120.0]})
    assert over.verdicts[duty] == "violation"
    assert over.missing_permits == ()
    under = evaluate(conn, BEFORE, case, permits={("operating_permit", "cod"): [200.0]})
    assert under.verdicts[duty] == "compliant"

    both = evaluate(conn, BEFORE, case, permits={("operating_permit", "cod"): [120.0, 200.0]})
    assert both.verdicts[duty] == "undetermined"
    assert ("contradictory_fact", "operating_permit") in both.problems
    other = evaluate(conn, BEFORE, case, permits={("construction_permit", "cod"): [120.0]})
    assert other.verdicts[duty] == "undetermined"


# 認定方式


def _labeled_limit(**labels: str) -> dict[str, Any]:
    limit = _limit("條2", 100)
    limit["conditions"][0] |= labels
    return limit


def _measured(conn: Conn, tmp_path: Path, **labels: str) -> int:
    _law(conn, tmp_path)
    doc = _with(_doc(_duty("條1"), _labeled_limit(**labels)), "cems", "manual", "hourly")
    return load(conn, doc).ids["duty"]


def test_a_labeled_limit_compares_only_facts_measured_its_way(conn: Conn, tmp_path: Path) -> None:
    """限值有標：事實的認定方式不同或沒給，要件未知、列為問題；相符才比較數值。"""
    duty = _measured(conn, tmp_path, method="cems", averaging="hourly")
    cond = int(
        _scalar(conn, "SELECT id FROM annotation.statement_condition WHERE condition_role = 'duty'")
    )
    case = {"discharging": 1.0, "cod": 150.0}

    def run(*given: Measurement) -> tuple[str, list[tuple[str, int | str]]]:
        result = evaluate(conn, BEFORE, case, measurements={"cod": list(given)})
        return result.verdicts[duty], list(result.problems)

    assert run() == ("undetermined", [("measurement_mismatch", cond)])
    assert run(Measurement("manual", "hourly")) == (
        "undetermined",
        [("measurement_mismatch", cond)],
    )
    assert run(Measurement("cems")) == ("undetermined", [("measurement_mismatch", cond)])
    assert run(Measurement("cems", "hourly")) == ("violation", [])
    assert run(Measurement("cems", "hourly"), Measurement("manual", "hourly"))[0] == "undetermined"


def test_an_unlabeled_limit_is_compared_with_an_open_issue(conn: Conn, tmp_path: Path) -> None:
    """限值沒有標：照常比較，結論附一則「認定方式未標註」。"""
    duty = _measured(conn, tmp_path)
    cond = int(
        _scalar(conn, "SELECT id FROM annotation.statement_condition WHERE condition_role = 'duty'")
    )

    result = evaluate(conn, BEFORE, {"discharging": 1.0, "cod": 150.0})
    assert result.verdicts[duty] == "violation"
    assert result.problems == (("measurement_unlabeled", cond),)
    assert evaluate(conn, BEFORE, {"discharging": 1.0}).problems == ()  # 沒有比較就沒有這則


def test_a_partly_labeled_limit_checks_only_what_it_labels(conn: Conn, tmp_path: Path) -> None:
    duty = _measured(conn, tmp_path, method="cems")

    result = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0},
        measurements={"cod": [Measurement("cems")]},
    )
    assert result.verdicts[duty] == "violation"
    assert result.problems == ()


def test_the_way_of_measuring_belongs_to_limits_only(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    duty = _plain_duty(text="條1")
    duty["conditions"][1]["method"] = "cems"
    with pytest.raises(AnnotationError, match="measurement_is_limit"):
        load(conn, _with(_doc(duty), "cems"))


def test_a_limit_lists_its_way_of_measuring(conn: Conn, tmp_path: Path) -> None:
    _measured(conn, tmp_path, method="cems", averaging="hourly")
    statements = list_statements(conn, _law_id(conn), BEFORE).statements
    (limit,) = [c for s in statements for c in s.limits]
    assert (limit.measurement_method, limit.averaging_period) == ("cems", "hourly")


# 效果的觸發與新增的效果種類


def _chain(**trigger: str) -> dict[str, Any]:
    """違反第 1 條者處罰鍰並限期改善；屆期未改善者按次處罰（以限期改善為前提）。"""
    order = _statement(
        "order",
        "art_2",
        "條2",
        kind="sanction",
        sanction={"punishes": "duty", "effect": "order"},
    )
    again = _statement(
        "again",
        "art_3",
        "條3",
        kind="sanction",
        conditions=[_scope("lapsed")],
        sanction={
            "punishes": "duty",
            "effect": "fine",
            "min": 1,
            "max": 2,
            "repeating": True,
            "triggers": [{"statement": "order", "kind": "order_lapsed", **trigger}],
        },
    )
    return _with(_doc(_plain_duty(text="條1"), order, again), "lapsed")


def test_a_lapsed_order_must_be_an_order(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    doc = _chain(statement="again")
    doc["statements"].append(
        _statement(
            "fine",
            "art_4",
            "條4",
            kind="sanction",
            sanction={"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
        )
    )
    doc["statements"][2]["sanction"]["triggers"] = [{"statement": "fine", "kind": "order_lapsed"}]
    with pytest.raises(AnnotationError, match="lapsed_is_order"):
        load(conn, doc)


def test_a_triggered_penalty_follows_its_trigger(conn: Conn, tmp_path: Path) -> None:
    """按次處罰以限期改善為前提：前提列出才列出，並帶前提；前提附條件則它也附條件。"""
    _law(conn, tmp_path)
    loaded = load(conn, _chain())
    assert loaded.triggers == 1
    order, again = loaded.ids["order"], loaded.ids["again"]
    breach = {"discharging": 1.0, "cod": 150.0, "lapsed": 1.0, "ap_intent": 1.0}

    listed = {p.statement_id: p for p in evaluate(conn, BEFORE, breach).penalties}
    assert listed[again].conditional is False
    assert listed[again].triggered_by == ((order, "order_lapsed"),)

    assert again not in {
        p.statement_id for p in evaluate(conn, BEFORE, breach | {"cod": 50.0}).penalties
    }
    lapse_unknown = {k: v for k, v in breach.items() if k != "lapsed"}
    assert {p.statement_id: p.conditional for p in evaluate(conn, BEFORE, lapse_unknown).penalties}[
        again
    ]


def test_a_penalty_whose_trigger_is_not_listed_is_not_listed(conn: Conn, tmp_path: Path) -> None:
    """另一處分已作成：前提的罰鍰因總則不罰時，以它為前提的環境講習也不列。"""
    _law(conn, tmp_path)
    fine = _statement(
        "fine",
        "art_2",
        "條2",
        kind="sanction",
        sanction={"punishes": "duty", "effect": "fine", "min": 5000, "max": 10000},
    )
    training = _statement(
        "training",
        "art_3",
        "條3",
        kind="sanction",
        sanction={
            "punishes": "duty",
            "effect": "warning",
            "triggers": [{"statement": "fine", "kind": "sanction_imposed"}],
        },
    )
    ids = load(conn, _doc(_plain_duty(text="條1"), fine, training)).ids
    breach = {"discharging": 1.0, "cod": 150.0}

    guilty = evaluate(conn, BEFORE, breach | {"ap_intent": 1.0})
    assert [(p.statement_id, p.effect) for p in guilty.penalties] == [
        (ids["fine"], "fine"),
        (ids["training"], "warning"),
    ]
    innocent = evaluate(conn, BEFORE, breach | {"ap_intent": 0.0, "ap_negligence": 0.0})
    assert innocent.penalties == ()
    assert innocent.general.unpunished == ((ids["fine"], "no_fault"),)  # 前提沒作成，講習不觸發
    unknown = evaluate(conn, BEFORE, breach)  # 有責性未知：罰鍰附條件，講習隨之附條件
    assert [(p.statement_id, p.conditional) for p in unknown.penalties] == [
        (ids["fine"], True),
        (ids["training"], True),
    ]


def test_disgorgement_is_not_an_administrative_penalty(conn: Conn, tmp_path: Path) -> None:
    """不法利得的追繳不以故意過失為要件：總則的排除與時效不適用；環境講習（warning）是行政罰，適用。"""
    _law(conn, tmp_path)
    recover = _statement(
        "recover",
        "art_2",
        "條2",
        kind="sanction",
        sanction={"punishes": "duty", "effect": "disgorgement"},
    )
    training = _statement(
        "training",
        "art_3",
        "條3",
        kind="sanction",
        sanction={"punishes": "duty", "effect": "warning"},
    )
    ids = load(conn, _doc(_plain_duty(text="條1"), recover, training)).ids

    result = evaluate(
        conn, BEFORE, {"discharging": 1.0, "cod": 150.0, "ap_intent": 0.0, "ap_negligence": 0.0}
    )
    assert [(p.statement_id, p.effect, p.conditional) for p in result.penalties] == [
        (ids["recover"], "disgorgement", False)
    ]
    assert result.general.unpunished == ((ids["training"], "no_fault"),)


def test_triggers_are_listed_with_the_sanction(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    ids = load(conn, _chain()).ids

    by_id = {s.id: s for s in list_statements(conn, _law_id(conn), BEFORE).statements}
    (trigger,) = by_id[ids["again"]].sanction.triggers
    assert (trigger.statement_id, trigger.kind) == (ids["order"], pb.TRIGGER_KIND_ORDER_LAPSED)


# 外部對映


def _cadmium_duty() -> dict[str, Any]:
    duty = _plain_duty(text="條1")
    duty["conditions"][1]["concept"] = "cadmium"
    return duty


def _matched(*concepts: dict[str, Any], duty: dict[str, Any]) -> dict[str, Any]:
    doc = _doc(duty)
    doc["concepts"] += list(concepts)
    return doc


def test_a_fact_on_an_equal_concept_counts(conn: Conn, tmp_path: Path) -> None:
    """兩個概念對到同一個 CAS 號，才視為同一個概念：一方的事實當成另一方的事實。"""
    _law(conn, tmp_path)
    cas = {"cas": "7440-43-9"}
    duty = load(
        conn,
        _matched(
            {"code": "cadmium", "label": "鎘", "matches": cas},
            {"code": "cd", "label": "鎘及其化合物", "matches": cas},
            {"code": "lead", "label": "鉛", "matches": {"cas": "7439-92-1"}},
            duty=_cadmium_duty(),
        ),
    ).ids["duty"]

    assert evaluate(conn, BEFORE, {"discharging": 1.0, "cd": 150.0}).verdicts[duty] == "violation"
    assert evaluate(conn, BEFORE, {"discharging": 1.0, "lead": 150.0}).verdicts[duty] == (
        "undetermined"
    )
    clash = evaluate(conn, BEFORE, {"discharging": 1.0, "cd": 150.0, "cadmium": 50.0})
    assert clash.verdicts[duty] == "undetermined"
    assert ("contradictory_fact", "cadmium") in clash.problems


def test_a_value_concept_matches_an_equal_value(conn: Conn, tmp_path: Path) -> None:
    """概念值也一樣：行業對到同一個行業統計分類代碼，就屬於比較的那個行業。"""
    _law(conn, tmp_path)
    code = {"tw_industry": "C1700"}
    duty = _plain_duty(text="條1")
    duty["conditions"][0] = {
        "role": "scope",
        "clause": 1,
        "concept": "industry",
        "comparator": "eq",
        "value_concept": "paper_mill",
    }
    sid = load(
        conn,
        _matched(
            {"code": "industry", "label": "行業"},
            {"code": "paper_mill", "label": "造紙業", "matches": code},
            {"code": "pulp_and_paper", "label": "紙漿、紙及紙製品製造業", "matches": code},
            duty=duty,
        ),
    ).ids["duty"]

    case: dict[str, float | str] = {"industry": "pulp_and_paper", "cod": 150.0}
    assert evaluate(conn, BEFORE, case).verdicts[sid] == "violation"


def test_a_concept_has_one_identifier_per_external_system(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(
        conn,
        _matched(
            {"code": "cadmium", "label": "鎘", "matches": {"cas": "7440-43-9"}},
            duty=_cadmium_duty(),
        ),
    )
    with pytest.raises(AnnotationError, match="已對映到 7440-43-9"):
        load(
            conn,
            _matched(
                {"code": "cadmium", "label": "鎘", "matches": {"cas": "1-1-1"}},
                duty=_cadmium_duty(),
            ),
        )
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept_match") == 1


# 可執行性閘門：新的形狀也要確認得了，否則它們永遠到不了推論


def test_the_executable_gate_runs_a_permit_threshold(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    loaded = load(conn, _with(_doc(_permitted(), review="extracted"), "operating_permit"))

    result = ExecutableGate().check(conn, loaded.ids["duty"])
    assert result is not None and result.verdict == "pass"


def test_the_executable_gate_measures_facts_the_limits_way(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _with(_doc(_labeled_limit(method="cems", averaging="hourly")), "cems", "hourly"))
    duty = load(conn, _doc(_duty("條1"), review="extracted")).ids["duty"]

    result = ExecutableGate().check(conn, duty)
    assert result is not None and result.verdict == "pass"


def test_the_executable_gate_lists_a_triggered_penalty_with_its_trigger(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)
    doc = _chain()
    doc["review"] = "extracted"
    loaded = _with_penalties(conn, doc)

    result = ExecutableGate().check(conn, loaded.ids["again"])
    assert result is not None and result.verdict == "pass"
    assert {c["case"] for c in result.evidence["cases"]} == {
        "scope holds",
        "lapsed not met",
        "scope unknown",
    }


def test_the_executable_gate_does_not_fail_a_chain_it_cannot_build(
    conn: Conn, tmp_path: Path
) -> None:
    """前提本身又有前提：閘門造不出案件，是無法判定，不是失敗（失敗會駁回合法的陳述）。"""
    _law(conn, tmp_path)
    doc = _chain()
    doc["review"] = "extracted"
    doc["statements"].append(
        _statement(
            "training",
            "art_4",
            "條4",
            kind="sanction",
            sanction={
                "punishes": "duty",
                "effect": "warning",
                "triggers": [{"statement": "again", "kind": "sanction_imposed"}],
            },
        )
    )
    loaded = _with_penalties(conn, doc)

    result = ExecutableGate().check(conn, loaded.ids["training"])
    assert result is not None and result.verdict == "inconclusive"


def test_the_reviewers_see_the_new_fields(conn: Conn, tmp_path: Path) -> None:
    """模型閘門讀的描述要含新欄位，否則它們未經審查就被確認。"""
    _law(conn, tmp_path)
    ids = load(conn, _chain()).ids
    remediate = load(conn, _doc(_remediation("state"))).ids["remediate"]

    assert "責任基礎：狀態責任" in render_annotation(conn, remediate)
    assert "前提：另一罰則 order（命令（例如限期改善）） 屆期未履行" in render_annotation(
        conn, ids["again"], for_reader=True
    )


# 審查補上的邊界


def test_a_penalty_whose_trigger_is_not_in_force_is_not_listed(conn: Conn, tmp_path: Path) -> None:
    """前提的陳述不在當日的陳述裡（被駁回、文字已改、法規未施行）：被觸發者不列，也不報缺事實。"""
    _law(conn, tmp_path)
    ids = load(conn, _chain()).ids
    conn.execute("SELECT annotation.set_confirm_state(%s, 'rejected', 'test')", (ids["order"],))
    conn.commit()

    result = evaluate(conn, BEFORE, {"discharging": 1.0, "cod": 150.0, "ap_intent": 1.0})
    assert ids["again"] not in {p.statement_id for p in result.penalties}
    assert (ids["again"], "lapsed") not in result.missing


def test_a_permit_value_under_an_equal_concept_counts(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    doc = _with(_doc(_permitted()), "operating_permit")
    doc["concepts"] += [
        {"code": "cod", "label": "化學需氧量", "matches": {"cas": "x-cod"}},
        {"code": "chemical_oxygen_demand", "label": "化學需氧量", "matches": {"cas": "x-cod"}},
        {"code": "op_permit", "label": "操作許可", "matches": {"tw_permit": "op"}},
    ]
    doc["concepts"] = [
        c
        for c in doc["concepts"]
        if c != {"code": "cod", "label": "化學需氧量", "aliases": ["COD"]}
    ]
    doc["concepts"] = [
        {**c, "matches": {"tw_permit": "op"}} if c["code"] == "operating_permit" else c
        for c in doc["concepts"]
    ]
    duty = load(conn, doc).ids["duty"]

    result = evaluate(
        conn,
        BEFORE,
        {"discharging": 1.0, "cod": 150.0},
        permits={("op_permit", "chemical_oxygen_demand"): [120.0]},
    )
    assert result.verdicts[duty] == "violation"


@pytest.mark.parametrize(
    ("enum", "document", "contract"),
    [
        ("legal_effect", LEGAL_EFFECTS, enums.LEGAL_EFFECT),
        ("liability_basis", LIABILITY_BASES, enums.LIABILITY_BASIS),
        ("trigger_kind", TRIGGER_KINDS, enums.TRIGGER_KIND),
    ],
)
def test_each_enum_has_one_list_of_values(
    conn: Conn, enum: str, document: tuple[str, ...], contract: dict[str, int]
) -> None:
    """資料庫的列舉、載入文件（抽取器的提示由它組成）與 proto 的對照表是同一組值。"""
    labels = conn.execute(
        "SELECT e.enumlabel FROM pg_enum AS e JOIN pg_type AS t ON t.oid = e.enumtypid"
        " JOIN pg_namespace AS n ON n.oid = t.typnamespace"
        " WHERE n.nspname = 'annotation' AND t.typname = %s",
        (enum,),
    ).fetchall()
    assert {str(r[0]) for r in labels} == set(document) == set(contract)
