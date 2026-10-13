"""ReasoningService 與錯誤模型：強型別的訊息、ErrorInfo／BadRequest、期限與名額。"""

from pathlib import Path
from typing import Any

import grpc
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc, reasoning_pb2, reasoning_pb2_grpc
from env_law_ontology import errors
from env_law_ontology.annotate import load
from env_law_ontology.reasoning_service import ReasoningServicer
from env_law_ontology.store import OntologyStore
from tests.conftest import auth
from tests.reasoning_support import Conn
from tests.test_annotation import LAW, _doc, _duty, _limit
from tests.test_incremental import _first, _law_v1
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
        reasoning_pb2.ListStatementsRequest(law_id=LAW), metadata=auth()
    )

    by_id = {s.id: s for s in response.statements}
    duty, limit = by_id[duty_id], by_id[limit_id]
    assert duty.kind == reasoning_pb2.STATEMENT_KIND_PRESCRIPTIVE
    assert duty.modality == reasoning_pb2.NORM_MODALITY_OBLIGATION
    assert duty.version_iri.startswith("https://env.law/eli/tw/law/")
    scope = duty.scope.any_of[0].all_of[0]
    assert (scope.concept, scope.comparator) == ("discharging", reasoning_pb2.COMPARATOR_EQ)
    assert scope.value.value == "1"
    delegated = duty.duty.any_of[0].all_of[0]
    assert delegated.delegate.law_id == LAW
    assert delegated.delegate.provision_path == "art_2"
    assert limit.kind == reasoning_pb2.STATEMENT_KIND_CONSTITUTIVE
    assert not limit.HasField("modality")
    assert (limit.limits[0].comparator, limit.limits[0].value.value) == (
        reasoning_pb2.COMPARATOR_LE,
        "100",
    )


def test_a_violation_is_a_candidate_with_its_source(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    duty_id, _ = _committed(conn, tmp_path)

    response = reasoning_stub.Evaluate(_case(), metadata=auth())

    assert [(v.statement_id, v.verdict) for v in response.at_decision.verdicts] == [
        (duty_id, reasoning_pb2.VERDICT_VIOLATION)
    ]
    assert not response.earlier
    sources = {s.statement_id: s.version_iri for s in response.at_decision.sources}
    assert sources[duty_id].startswith("https://env.law/eli/tw/law/")
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

    response = reasoning_stub.Evaluate(request, metadata=auth())

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

    response = reasoning_stub.Evaluate(request, metadata=auth())

    assert not response.earlier[0].outcome.verdicts
    assert list(response.earlier[0].outcome.uncovered_law_ids) == [LAW]
    assert not response.at_decision.uncovered_law_ids


def test_no_annotation_means_no_conclusion(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _first(conn, tmp_path, _law_v1())
    conn.commit()

    response = reasoning_stub.Evaluate(_case(), metadata=auth())

    assert not response.at_decision.verdicts
    assert not response.at_decision.penalties


def test_an_unknown_law_and_an_uncovered_date_are_different_reasons(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    _committed(conn, tmp_path)

    with pytest.raises(grpc.RpcError) as unknown:
        reasoning_stub.ListStatements(
            reasoning_pb2.ListStatementsRequest(law_id="Z9999999"), metadata=auth()
        )
    with pytest.raises(grpc.RpcError) as early:
        reasoning_stub.ListStatements(
            reasoning_pb2.ListStatementsRequest(law_id=LAW, as_of="1900-01-01"), metadata=auth()
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
        reasoning_stub.Evaluate(request, metadata=auth())

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(error.value)
    assert info.reason == errors.INVALID_REQUEST
    assert fields == [
        "conduct_on",
        "decided_on",
        "facts[0].concept",
    ]


def test_conduct_after_decision_is_a_date_order_violation(reasoning_stub: Stub) -> None:
    with pytest.raises(grpc.RpcError) as error:
        reasoning_stub.Evaluate(
            _case(conduct_on="2020-01-02", decided_on="2020-01-01"), metadata=auth()
        )

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _details(error.value)[1] == ["conduct_on"]


def test_the_existing_service_uses_the_same_error_model(
    stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(ontology_pb2.GetLawRequest(id="O0020001", as_of="yesterday"), metadata=auth())

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
    servicer = ReasoningServicer(store, evaluate_slots=1)
    assert servicer._slots.acquire()  # 名額被占著

    with pytest.raises(_Aborted) as aborted:
        servicer.Evaluate(_case(), _Context(remaining=0.05))  # type: ignore[arg-type]

    assert aborted.value.status.code == grpc.StatusCode.RESOURCE_EXHAUSTED
