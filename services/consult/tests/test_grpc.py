import os
import signal
from collections.abc import Iterator
from concurrent import futures

import grpc
import pytest

from env_law.consult.v2 import consult_pb2, consult_pb2_grpc
from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law_consult.adapters import GrpcOntology
from env_law_consult.server import build_server
from env_law_grpc import stop_on_sigterm
from tests.conftest import FakeOntology, call_metadata


def _ground(
    stub: consult_pb2_grpc.ConsultationServiceStub, question: str = "露天燃燒如何處罰"
) -> consult_pb2.GroundResponse:
    response: consult_pb2.GroundResponse = stub.Ground(
        consult_pb2.GroundRequest(question=question), metadata=call_metadata()
    )
    return response


def test_evidence_carries_the_closed_set_with_text(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    response = _ground(stub)

    assert response.WhichOneof("outcome") == "evidence"
    citations = response.evidence.citations
    assert [c.code for c in citations] == ["L1", "L2"]
    assert citations[0].label == "空氣污染防制法 第 1 條"
    assert citations[0].text == "第 1 條條文內容。"
    assert {c.ontology for c in citations} == {"law"}
    assert response.evidence.confidence == consult_pb2.CONFIDENCE_HIGH


def test_law_ontology_may_be_requested_explicitly(
    stub: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    response = stub.Ground(
        consult_pb2.GroundRequest(question="露天燃燒如何處罰", ontologies=["law"]),
        metadata=call_metadata(),
    )

    assert response.WhichOneof("outcome") == "evidence"


def test_unknown_ontology_is_rejected(
    stub: consult_pb2_grpc.ConsultationServiceStub, ontology: FakeOntology
) -> None:
    with pytest.raises(grpc.RpcError) as err:
        stub.Ground(
            consult_pb2.GroundRequest(question="x", ontologies=["law", "case"]),
            metadata=call_metadata(),
        )

    assert err.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert "case" in (err.value.details() or "")
    assert ontology.searches == []


def test_blank_question_is_refused_not_an_error(
    stub: consult_pb2_grpc.ConsultationServiceStub, ontology: FakeOntology
) -> None:
    response = _ground(stub, question="  ")

    assert response.WhichOneof("outcome") == "refused"
    assert response.refused.reason == consult_pb2.Refused.REASON_MISSING_INPUT
    assert ontology.searches == []


def test_missing_tenant_is_rejected(stub: consult_pb2_grpc.ConsultationServiceStub) -> None:
    with pytest.raises(grpc.RpcError) as err:
        stub.Ground(consult_pb2.GroundRequest(question="x"), metadata=())

    assert err.value.code() is grpc.StatusCode.INVALID_ARGUMENT


def test_one_call_retrieves_once_at_the_requested_date(
    stub: consult_pb2_grpc.ConsultationServiceStub, ontology: FakeOntology
) -> None:
    request = consult_pb2.GroundRequest(question="露天燃燒如何處罰", as_of="2020-01-01")
    stub.Ground(request, metadata=call_metadata())

    assert ontology.searches == [("露天燃燒如何處罰", "2020-01-01")]


class _RejectingOntology(ontology_pb2_grpc.OntologyServiceServicer):
    def SearchArticles(
        self, request: ontology_pb2.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.SearchArticlesResponse:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "as_of must be an ISO 8601 date")


@pytest.fixture
def stub_over_rejecting_ontology() -> Iterator[consult_pb2_grpc.ConsultationServiceStub]:
    upstream = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    ontology_pb2_grpc.add_OntologyServiceServicer_to_server(_RejectingOntology(), upstream)  # type: ignore[abstract]
    upstream_port = upstream.add_insecure_port("127.0.0.1:0")
    upstream.start()
    with grpc.insecure_channel(f"127.0.0.1:{upstream_port}") as upstream_channel:
        server, port = build_server(GrpcOntology(upstream_channel), "127.0.0.1:0")
        server.start()
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield consult_pb2_grpc.ConsultationServiceStub(channel)
        server.stop(grace=None)
    upstream.stop(grace=None)


def test_ontology_status_passes_through(
    stub_over_rejecting_ontology: consult_pb2_grpc.ConsultationServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as err:
        stub_over_rejecting_ontology.Ground(
            consult_pb2.GroundRequest(question="排放", as_of="not-a-date"), metadata=call_metadata()
        )

    assert err.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert err.value.details() == "as_of must be an ISO 8601 date"


def test_sigterm_stops_the_server(ontology: FakeOntology) -> None:
    server, _ = build_server(ontology, "127.0.0.1:0")
    previous = signal.getsignal(signal.SIGTERM)
    try:
        stop_on_sigterm(server)
        server.start()
        os.kill(os.getpid(), signal.SIGTERM)

        assert server.wait_for_termination(timeout=5) is False
    finally:
        signal.signal(signal.SIGTERM, previous)
