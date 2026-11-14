"""ReasoningService 與錯誤模型：強型別的訊息、ErrorInfo／BadRequest、期限與名額。"""

from datetime import date
from pathlib import Path
from typing import Any

import grpc
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc, reasoning_pb2, reasoning_pb2_grpc
from envcite_ontology import errors
from envcite_ontology.annotate import load
from envcite_ontology.reasoning.facts.chains import Chain, Link
from envcite_ontology.reasoning_codec import outcome as outcome_codec
from envcite_ontology.reasoning_service import ReasoningServicer
from envcite_ontology.store.ontology_store import OntologyStore
from tests.conftest import call_metadata, identifier
from tests.reasoning_support import Conn, _plain_duty
from tests.test_annotation import LAW, _doc, _duty, _limit
from tests.test_incremental import _articles, _first, _law_v1
from tests.test_reasoning_transition import _amended

Stub = reasoning_pb2_grpc.ReasoningServiceStub


def _committed(conn: Conn, tmp_path: Path) -> tuple[int, int]:
    """噪音管制法：義務（引用第 2 條的限值）與限值 100，已確認並提交，伺服器看得到。"""
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_duty(), _limit("乙一", 100)))
    conn.commit()
    return loaded.ids["duty"], loaded.ids["limit100"]


def _details(error: grpc.RpcError) -> tuple[error_details_pb2.ErrorInfo, list[str]]:
    """錯誤的 ErrorInfo，與 BadRequest 裡每個違規欄位的路徑。"""
    status = rpc_status.from_call(error)  # type: ignore[arg-type]
    assert status is not None
    info = error_details_pb2.ErrorInfo()
    fields: list[str] = []
    for detail in status.details:
        if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
            detail.Unpack(info)
        elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
            bad = error_details_pb2.BadRequest()
            detail.Unpack(bad)
            fields += [v.field for v in bad.field_violations]
    return info, fields


def _case(**over: Any) -> reasoning_pb2.EvaluateRequest:
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="2019-01-01",
        decided_on="2019-06-01",
        facts=[
            reasoning_pb2.Fact(concept="discharging", values=[reasoning_pb2.FactValue(flag=True)]),
            reasoning_pb2.Fact(
                concept="cod",
                values=[reasoning_pb2.FactValue(number=reasoning_pb2.Decimal(value="150"))],
            ),
        ],
    )
    for key, value in over.items():
        setattr(request, key, value)
    return request


def test_statements_come_back_typed(conn: Conn, tmp_path: Path, reasoning_stub: Stub) -> None:
    duty_id, limit_id = _committed(conn, tmp_path)

    response = reasoning_stub.ListStatements(
        reasoning_pb2.ListStatementsRequest(identifier=identifier(LAW)), metadata=call_metadata()
    )

    by_id = {s.id: s for s in response.statements}
    duty, limit = by_id[duty_id], by_id[limit_id]
    assert duty.kind == reasoning_pb2.STATEMENT_KIND_PRESCRIPTIVE
    assert duty.modality == reasoning_pb2.NORM_MODALITY_OBLIGATION
    assert duty.version_iri.startswith("https://envcite/tw/law/")
    scope = duty.scope.any_of[0].all_of[0]
    assert (scope.concept, scope.comparator) == ("discharging", reasoning_pb2.COMPARATOR_EQ)
    assert scope.value.value == "1"
    delegated = duty.duty.any_of[0].all_of[0]
    assert delegated.delegate.identifier == identifier(LAW)
    assert delegated.delegate.provision_path == "art_2"
    assert limit.kind == reasoning_pb2.STATEMENT_KIND_CONSTITUTIVE
    assert not limit.HasField("modality")
    assert (limit.limits[0].comparator, limit.limits[0].value.value) == (
        reasoning_pb2.COMPARATOR_LE,
        "100",
    )


def test_a_belonging_comes_back_with_the_definition_it_rests_on(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    from tests.test_reasoning import _load_belonging

    _first(conn, tmp_path, _law_v1())
    _load_belonging(conn)
    conn.commit()
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="2019-01-01",
        decided_on="2019-06-01",
        facts=[
            reasoning_pb2.Fact(concept="discharging", values=[reasoning_pb2.FactValue(flag=True)]),
            reasoning_pb2.Fact(
                concept="industry", values=[reasoning_pb2.FactValue(concept="factory")]
            ),
        ],
    )

    response = reasoning_stub.Evaluate(request, metadata=call_metadata())

    (used,) = response.at_decision.definitions_used
    assert used.term == "受管制事業"
    assert list(used.members) == ["工廠"]
    assert used.version_iri.startswith("https://envcite/tw/law/")
    assert used.version_iri.endswith("/art_1")  # 定義所讀自的條文版本


def test_a_violation_is_a_candidate_with_its_source(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    duty_id, _ = _committed(conn, tmp_path)

    response = reasoning_stub.Evaluate(_case(), metadata=call_metadata())

    assert [(v.statement_id, v.verdict) for v in response.at_decision.verdicts] == [
        (duty_id, reasoning_pb2.VERDICT_VIOLATION)
    ]
    assert not response.earlier
    sources = {s.statement_id: s.version_iri for s in response.at_decision.sources}
    assert sources[duty_id].startswith("https://envcite/tw/law/")
    (cod,) = [t for t in response.at_decision.trace if t.concept == "cod"]
    assert (cod.fact.value, cod.threshold.value) == ("150", "100")
    assert cod.role == reasoning_pb2.CONDITION_ROLE_DUTY


def test_a_law_changed_after_the_conduct_is_reported_for_both_times(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _duty_id, _ = _amended(conn, tmp_path)
    conn.commit()
    request = _case(conduct_on="2019-01-01", decided_on="2020-06-01")
    request.facts[1].values[0].number.value = "90"

    response = reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert response.at_decision.verdicts[0].verdict == reasoning_pb2.VERDICT_VIOLATION
    (before,) = response.earlier
    assert before.first_day == "2019-01-01"
    assert before.outcome.verdicts[0].verdict == reasoning_pb2.VERDICT_COMPLIANT


def test_a_day_the_corpus_does_not_cover_is_named_not_silently_empty(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _amended(conn, tmp_path)
    conn.commit()
    request = _case(conduct_on="2010-01-01", decided_on="2020-06-01")

    response = reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert not response.earlier[0].outcome.verdicts
    assert list(response.earlier[0].outcome.uncovered_law_identifiers) == [identifier(LAW)]
    assert not response.at_decision.uncovered_law_identifiers


def test_no_annotation_means_no_conclusion(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _first(conn, tmp_path, _law_v1())
    conn.commit()

    response = reasoning_stub.Evaluate(_case(), metadata=call_metadata())

    assert not response.at_decision.verdicts
    assert not response.at_decision.penalties


def test_an_unknown_law_and_an_uncovered_date_are_different_reasons(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _committed(conn, tmp_path)

    with pytest.raises(grpc.RpcError) as unknown:
        reasoning_stub.ListStatements(
            reasoning_pb2.ListStatementsRequest(identifier=identifier("Z9999999")),
            metadata=call_metadata(),
        )
    with pytest.raises(grpc.RpcError) as early:
        reasoning_stub.ListStatements(
            reasoning_pb2.ListStatementsRequest(identifier=identifier(LAW), as_of="1900-01-01"),
            metadata=call_metadata(),
        )

    assert unknown.value.code() == grpc.StatusCode.NOT_FOUND
    assert _details(unknown.value)[0].reason == errors.LAW_NOT_FOUND
    assert early.value.code() == grpc.StatusCode.NOT_FOUND
    info, _ = _details(early.value)
    assert (info.reason, info.domain) == (errors.AS_OF_NOT_COVERED, errors.DOMAIN)


def test_every_invalid_field_is_reported_at_once(reasoning_stub: Stub) -> None:
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="not-a-date",
        facts=[
            reasoning_pb2.Fact(concept="ap_intent", values=[reasoning_pb2.FactValue(flag=True)]),
            reasoning_pb2.Fact(
                concept="cod",
                values=[reasoning_pb2.FactValue(number=reasoning_pb2.Decimal(value="1e999999"))],
            ),
        ],
    )

    with pytest.raises(grpc.RpcError) as error:
        reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(error.value)
    assert info.reason == errors.INVALID_REQUEST
    assert fields == [
        "conduct_on",
        "decided_on",
        "facts[0].concept",
    ]


def test_a_concept_value_fact_is_accepted_and_an_empty_one_is_reported(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _committed(conn, tmp_path)
    ok = _case()
    ok.facts.append(
        reasoning_pb2.Fact(concept="industry", values=[reasoning_pb2.FactValue(concept="factory")])
    )
    reasoning_stub.Evaluate(ok, metadata=call_metadata())

    bad = _case()
    bad.facts.append(
        reasoning_pb2.Fact(concept="industry", values=[reasoning_pb2.FactValue(concept="")])
    )
    with pytest.raises(grpc.RpcError) as error:
        reasoning_stub.Evaluate(bad, metadata=call_metadata())

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    _, fields = _details(error.value)
    assert any(f.endswith(".concept") for f in fields)


def test_conduct_after_decision_is_a_date_order_violation(reasoning_stub: Stub) -> None:
    with pytest.raises(grpc.RpcError) as error:
        reasoning_stub.Evaluate(
            _case(conduct_on="2020-01-02", decided_on="2020-01-01"), metadata=call_metadata()
        )

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(error.value)
    assert info.reason == errors.INVALID_REQUEST
    assert fields == ["conduct_on"]


def test_the_existing_service_uses_the_same_error_model(
    stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(
            ontology_pb2.GetLawRequest(identifier=identifier("O0020001"), as_of="yesterday"),
            metadata=call_metadata(),
        )

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _details(error.value) == (
        _details(error.value)[0],
        ["as_of"],
    )
    assert _details(error.value)[0].reason == errors.INVALID_REQUEST


class _Aborted(Exception):
    def __init__(self, status: Any) -> None:
        self.status = status


class _Context:
    """`Evaluate` 在名額用盡時只用到這幾個方法。"""

    def __init__(self, remaining: float | None) -> None:
        self._remaining = remaining

    def invocation_metadata(self) -> tuple[()]:
        return ()

    def time_remaining(self) -> float | None:
        return self._remaining

    def is_active(self) -> bool:
        return True

    def abort_with_status(self, status: Any) -> None:
        raise _Aborted(status)


def test_a_call_that_cannot_get_a_slot_in_its_budget_fails_fast(store: OntologyStore) -> None:
    servicer = ReasoningServicer(store.connections, store.laws, evaluate_slots=1)
    assert servicer._slots.acquire()  # 名額被占著

    with pytest.raises(_Aborted) as aborted:
        servicer.Evaluate(_case(), _Context(remaining=0.05))  # type: ignore[arg-type]

    assert aborted.value.status.code == grpc.StatusCode.RESOURCE_EXHAUSTED


def test_permit_limits_and_ways_of_measuring_reach_the_evaluation(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    """許可量值與事實的認定方式隨請求進來；缺的許可量值與認定方式的問題隨結果出去。"""
    _first(conn, tmp_path, _law_v1())
    limit = _limit("乙一", 100)
    limit["conditions"][0]["method"] = "cems"
    doc = _doc(_duty(), limit)
    doc["concepts"].append({"code": "cems", "label": "連續自動監測"})
    duty = load(conn, doc).ids["duty"]
    conn.commit()

    unmeasured = reasoning_stub.Evaluate(_case(), metadata=call_metadata()).at_decision
    assert [(v.statement_id, v.verdict) for v in unmeasured.verdicts] == [
        (duty, reasoning_pb2.VERDICT_UNDETERMINED)
    ]
    assert [p.kind for p in unmeasured.problems] == [
        reasoning_pb2.PROBLEM_KIND_MEASUREMENT_MISMATCH
    ]

    request = _case()
    request.facts[1].measurement_method = "cems"
    measured = reasoning_stub.Evaluate(request, metadata=call_metadata()).at_decision
    assert [v.verdict for v in measured.verdicts] == [reasoning_pb2.VERDICT_VIOLATION]
    assert list(measured.problems) == []


def test_a_permit_limit_without_a_value_is_an_invalid_argument(reasoning_stub: Stub) -> None:
    request = _case()
    request.permit_limits.add(permit="operating_permit", concept="cod")
    with pytest.raises(grpc.RpcError) as caught:
        reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _details(caught.value)[1] == ["permit_limits[0].value.value"]


def test_an_unspecified_actor_basis_is_an_invalid_argument(reasoning_stub: Stub) -> None:
    request = _case()
    request.actor_bases.extend(
        [reasoning_pb2.LIABILITY_BASIS_ACT, reasoning_pb2.LIABILITY_BASIS_UNSPECIFIED]
    )
    with pytest.raises(grpc.RpcError) as caught:
        reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _details(caught.value)[1] == ["actor_bases[1]"]


def test_the_penalty_law_facts_reach_the_evaluation_and_come_back_typed(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    """第 18、26 條：減輕的範圍、加重的上限、扣抵額與刑事優先，都經契約往返。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    penalty = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1000, "max": 60000},
    }
    fine = load(conn, _doc(_plain_duty(), penalty)).ids["fine"]
    conn.commit()

    def money(value: str) -> reasoning_pb2.Decimal:
        return reasoning_pb2.Decimal(value=value)

    cleared = _case(conduct_on="2019-01-01", decided_on="2019-01-01")
    cleared.administrative_penalty_facts.CopyFrom(
        reasoning_pb2.AdministrativePenaltyFacts(
            intent=True,
            age=16,
            criminal_offense=True,
            criminal_cleared_on="2018-12-01",
            criminal_payment=money("5000"),
            community_service_hours=money("2"),
            minimum_hourly_wage=money("150.5"),
            gained_benefit=money("90000"),
        )
    )
    general = reasoning_stub.Evaluate(cleared, metadata=call_metadata()).at_decision.general
    (reduced,) = general.reduced
    assert (reduced.penalty_statement_id, reduced.share) == (
        fine,
        reasoning_pb2.REDUCTION_SHARE_HALF,
    )
    assert (reduced.min_amount, reduced.max_amount) == (500, 30000)
    assert [(c.penalty_statement_id, c.max_amount.value) for c in general.raised_caps] == [
        (fine, "90000")
    ]
    assert [(d.penalty_statement_id, d.amount.value) for d in general.criminal_deductions] == [
        (fine, "5301")
    ]

    pending = _case(conduct_on="2019-01-01", decided_on="2019-01-01")
    pending.administrative_penalty_facts.CopyFrom(
        reasoning_pb2.AdministrativePenaltyFacts(intent=True, criminal_offense=True)
    )
    outcome = reasoning_stub.Evaluate(pending, metadata=call_metadata()).at_decision
    assert not outcome.penalties
    assert [(u.penalty_statement_id, u.excuse) for u in outcome.general.unpunished] == [
        (fine, reasoning_pb2.EXCUSE_CRIMINAL_PRIORITY)
    ]


def test_invalid_penalty_law_facts_are_reported(reasoning_stub: Stub) -> None:
    request = _case()
    request.administrative_penalty_facts.CopyFrom(
        reasoning_pb2.AdministrativePenaltyFacts(
            criminal_cleared_on="yesterday",
            gained_benefit=reasoning_pb2.Decimal(value="-1"),
            criminal_payment=reasoning_pb2.Decimal(value="many"),
        )
    )

    with pytest.raises(grpc.RpcError) as error:
        reasoning_stub.Evaluate(request, metadata=call_metadata())

    _, fields = _details(error.value)
    assert fields == [
        "administrative_penalty_facts.criminal_cleared_on",
        "administrative_penalty_facts.criminal_payment.value",
        "administrative_penalty_facts.gained_benefit",
    ]


def test_an_invalidated_limit_comes_back_with_its_ruling(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    duty_id, limit_id = _committed(conn, tmp_path)
    ruling = conn.execute(
        "INSERT INTO corpus.constitutional_ruling"
        " (ruling_no, ruling_kind, decided_on, source_url, excerpt)"
        " VALUES ('憲判字第 99 號', 'judgment', '2019-02-01', 'https://cons.judicial.gov.tw/x',"
        " '失其效力') RETURNING id"
    ).fetchone()
    assert ruling is not None
    conn.execute(
        "INSERT INTO corpus.ruling_invalidation"
        " (ruling_id, resource_id, provision_id, invalid_from)"
        " SELECT %s, resource_id, id, '2019-03-01' FROM corpus.provision WHERE path = 'art_2'",
        (ruling[0],),
    )
    conn.commit()

    outcome = reasoning_stub.Evaluate(_case(), metadata=call_metadata()).at_decision

    assert [(v.statement_id, v.verdict) for v in outcome.verdicts] == [
        (duty_id, reasoning_pb2.VERDICT_UNDETERMINED)
    ]
    assert [p.kind for p in outcome.problems] == [reasoning_pb2.PROBLEM_KIND_INVALIDATED_DELEGATION]
    assert [(i.statement_id, i.ruling_no, i.invalid_from) for i in outcome.invalidated] == [
        (limit_id, "憲判字第 99 號", "2019-03-01")
    ]


def test_a_missing_local_id_is_reported(reasoning_stub: Stub) -> None:
    request = reasoning_pb2.ListStatementsRequest(
        identifier=identifier("", jurisdiction="TW-TPE", id_scheme="local_glrs"),
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub.ListStatements(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(err.value)
    assert info.reason == errors.INVALID_REQUEST
    assert "identifier.local_id" in fields


def test_a_local_law_that_does_not_exist_is_not_found(reasoning_stub: Stub) -> None:
    request = reasoning_pb2.ListStatementsRequest(
        identifier=identifier("NON_EXISTENT_999", jurisdiction="TW-TPE", id_scheme="local_glrs"),
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub.ListStatements(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.NOT_FOUND
    info, _ = _details(err.value)
    assert info.reason == errors.LAW_NOT_FOUND


def test_an_unparseable_number_is_reported_with_its_path(reasoning_stub: Stub) -> None:
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="not-a-date",
        facts=[
            reasoning_pb2.Fact(concept="ap_intent", values=[reasoning_pb2.FactValue(flag=True)]),
            reasoning_pb2.Fact(
                concept="cod",
                values=[reasoning_pb2.FactValue(number=reasoning_pb2.Decimal(value="bad-num"))],
            ),
        ],
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub.Evaluate(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(err.value)
    assert info.reason == errors.INVALID_REQUEST
    assert "conduct_on" in fields
    assert "decided_on" in fields
    assert "facts[0].concept" in fields
    assert "facts[1].values[0].number.value" in fields


def test_codec_carries_the_identifiers_of_central_and_local_laws() -> None:
    """測試推論編碼器原生支援中央與地方雙軌授權鏈與未涵蓋法規識別碼。"""
    from envcite_ontology.reasoning import Result

    central_act = Link(
        command_jurisdiction="TW-TPE",
        command_id_scheme="local_glrs",
        command_local_id="TPE-ENV-001",
        cited_law_name="空氣污染防制法",
        cited_path="art_20",
        target_jurisdiction="TW-CENTRAL",
        target_id_scheme="moj_pcode",
        target_local_id="O0020001",
        version_iri="https://envcite/tw/law/O0020001/2018-08-01/zho/art_20",
        gap=None,
    )
    chain = Chain(rooted=True, links=(central_act,))
    basis = outcome_codec._delegation(statement_id=101, chain=chain)

    assert basis.statement_id == 101
    assert basis.rooted is True
    assert len(basis.links) == 1
    link = basis.links[0]
    assert link.command_identifier.jurisdiction == "TW-TPE"
    assert link.command_identifier.id_scheme == "local_glrs"
    assert link.command_identifier.local_id == "TPE-ENV-001"
    assert link.identifier.jurisdiction == "TW-CENTRAL"
    assert link.identifier.id_scheme == "moj_pcode"
    assert link.identifier.local_id == "O0020001"
    assert link.cited_law_name == "空氣污染防制法"

    # outcome 原生封裝 uncovered_law_identifiers
    from envcite_ontology.reasoning.engine import General

    general = General(unpunished=(), mitigation=(), presumed=(), time_barred=())
    result = Result(
        verdicts={101: "violation"},
        penalties=(),
        one_act=None,
        missing=(),
        needs_judgement=(),
        judgement_used=(),
        problems=(),
        unresolved_conflicts=(),
        defeated=(),
        unresolved_penalty_overlaps=(),
        penalty_defeated=(),
        trace=(),
        limit_sources={},
        general=general,
        delegation={101: chain},
    )
    uncovered = (
        ("TW-CENTRAL", "moj_pcode", "O0020001"),
        ("TW-TPE", "local_glrs", "TPE-ENV-001"),
    )
    out = outcome_codec.outcome(result, sources={101: "https://iri"}, uncovered=uncovered)
    assert len(out.uncovered_law_identifiers) == 2
    assert out.uncovered_law_identifiers[0].jurisdiction == "TW-CENTRAL"
    assert out.uncovered_law_identifiers[0].local_id == "O0020001"
    assert out.uncovered_law_identifiers[1].jurisdiction == "TW-TPE"
    assert out.uncovered_law_identifiers[1].local_id == "TPE-ENV-001"
    assert len(out.delegation_bases) == 1
    assert out.delegation_bases[0].links[0].command_identifier.jurisdiction == "TW-TPE"
    assert out.delegation_bases[0].links[0].identifier.jurisdiction == "TW-CENTRAL"


def test_the_default_day_of_a_listing_is_the_injected_today(
    conn: Conn, tmp_path: Path, store: OntologyStore
) -> None:
    _committed(conn, tmp_path)
    request = reasoning_pb2.ListStatementsRequest(identifier=identifier(LAW))

    now = ReasoningServicer(store.connections, store.laws)
    long_ago = ReasoningServicer(store.connections, store.laws, today=lambda: date(1900, 1, 1))

    assert now.ListStatements(request, _Context(remaining=5.0)).statements  # type: ignore[arg-type]
    assert not long_ago.ListStatements(request, _Context(remaining=5.0)).statements  # type: ignore[arg-type]
