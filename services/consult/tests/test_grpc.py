import grpc
import pytest

from env_law.consult.v1 import consult_pb2, consult_pb2_grpc
from tests.conftest import auth


def _events(
    stub: consult_pb2_grpc.ConsultationServiceStub, question: str = "露天燃燒如何處罰"
) -> list[consult_pb2.ConsultResponse]:
    request = consult_pb2.ConsultRequest(question=question)
    return list(stub.Consult(request, metadata=auth()))


def test_grounding_is_emitted_before_any_answer_chunk(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    kinds = [e.WhichOneof("event") for e in _events(stub)]

    assert kinds.index("grounding") < kinds.index("answer_chunk")


def test_completed_codes_are_a_subset_of_grounding(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    events = _events(stub)
    offered = {
        c.code
        for e in events
        if e.WhichOneof("event") == "grounding"
        for c in e.grounding.citations
    }
    cited = {
        code
        for e in events
        if e.WhichOneof("event") == "completed"
        for code in e.completed.cited_codes
    }

    assert cited and cited <= offered


def test_blank_question_is_refused_not_answered(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    events = _events(stub, question="  ")
    kinds = [e.WhichOneof("event") for e in events]

    assert "refused" in kinds
    assert "answer_chunk" not in kinds


def test_missing_tenant_is_rejected(stub: consult_pb2_grpc.ConsultationServiceStub) -> None:
    metadata = (("authorization", f"Bearer {'test-internal-token'}"),)
    with pytest.raises(grpc.RpcError) as err:
        list(stub.Consult(consult_pb2.ConsultRequest(question="x"), metadata=metadata))

    assert err.value.code() is grpc.StatusCode.INVALID_ARGUMENT


def test_missing_token_is_unauthenticated(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as err:
        list(stub.Consult(consult_pb2.ConsultRequest(question="x"), metadata=()))

    assert err.value.code() is grpc.StatusCode.UNAUTHENTICATED
