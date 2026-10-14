import os
import signal

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from envcite.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from envcite_grpc import stop_on_sigterm
from envcite_ontology.server import build_server
from envcite_ontology.store import OntologyStore

from .conftest import call_metadata

pytestmark = pytest.mark.usefixtures("sample")


def test_get_law(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    response = stub.GetLaw(ontology_pb2.GetLawRequest(id="O0020001"), metadata=call_metadata())

    assert response.law.title == "空氣污染防制法"
    assert response.law.id == "O0020001"


def test_list_articles(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    response = stub.ListArticles(
        ontology_pb2.ListArticlesRequest(law_id="O0020001"), metadata=call_metadata()
    )

    assert [a.number for a in response.articles] == ["1", "100"]


def test_unknown_law_is_not_found(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(ontology_pb2.GetLawRequest(id="Z9999999"), metadata=call_metadata())

    assert error.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize(
    "service", ["envcite.ontology.v1.OntologyService", "envcite.ontology.v1.ReasoningService"]
)
def test_health_check_reports_serving(channel: grpc.Channel, service: str) -> None:
    health_stub = health_pb2_grpc.HealthStub(channel)

    response = health_stub.Check(  # type: ignore[attr-defined]
        health_pb2.HealthCheckRequest(service=service)
    )

    assert response.status == health_pb2.HealthCheckResponse.SERVING


def test_sigterm_stops_the_server(store: OntologyStore) -> None:
    server, _ = build_server(store, "127.0.0.1:0")
    previous = signal.getsignal(signal.SIGTERM)
    try:
        stop_on_sigterm(server)
        server.start()
        os.kill(os.getpid(), signal.SIGTERM)

        assert server.wait_for_termination(timeout=5) is False
    finally:
        signal.signal(signal.SIGTERM, previous)
