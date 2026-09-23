import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc

from .conftest import auth


def test_get_law(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    response = stub.GetLaw(
        ontology_pb2.GetLawRequest(id="air-pollution-control-act"), metadata=auth()
    )

    assert response.law.title == "空氣污染防制法"
    assert response.law.id == "air-pollution-control-act"


def test_list_articles(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    response = stub.ListArticles(
        ontology_pb2.ListArticlesRequest(law_id="air-pollution-control-act"), metadata=auth()
    )

    assert [a.number for a in response.articles] == ["1", "2", "5-1", "10"]


def test_unknown_law_is_not_found(stub: ontology_pb2_grpc.OntologyServiceStub) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(ontology_pb2.GetLawRequest(id="unknown-act"), metadata=auth())

    assert error.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize(
    "metadata",
    [(), (("authorization", "Bearer wrong"),), (("authorization", "test-internal-token"),)],
)
def test_missing_or_wrong_token_is_unauthenticated(
    stub: ontology_pb2_grpc.OntologyServiceStub, metadata: tuple[tuple[str, str], ...]
) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(ontology_pb2.GetLawRequest(id="air-pollution-control-act"), metadata=metadata)

    assert error.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_health_check_needs_no_token(channel: grpc.Channel) -> None:
    health_stub = health_pb2_grpc.HealthStub(channel)

    response = health_stub.Check(  # type: ignore[attr-defined]
        health_pb2.HealthCheckRequest(service="env_law.ontology.v1.OntologyService")
    )

    assert response.status == health_pb2.HealthCheckResponse.SERVING
