"""過渡：舊套件名 `envcite.ontology.v2` 的呼叫端在升級前仍得到同樣的回應。"""

import grpc
import pytest

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc, reasoning_pb2, reasoning_pb2_grpc
from tests.conftest import call_metadata

pytestmark = pytest.mark.usefixtures("sample")


def test_the_old_package_name_still_answers(channel: grpc.Channel) -> None:
    stub = ontology_pb2_grpc.OntologyServiceStub(channel)
    laws = stub.ListLaws(ontology_pb2.ListLawsRequest(), metadata=call_metadata())
    assert laws.laws


def test_the_old_package_name_keeps_error_details(channel: grpc.Channel) -> None:
    stub = ontology_pb2_grpc.OntologyServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.GetLaw(ontology_pb2.GetLawRequest(), metadata=call_metadata())
    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_the_old_reasoning_service_name_answers(channel: grpc.Channel) -> None:
    stub = reasoning_pb2_grpc.ReasoningServiceStub(channel)
    with pytest.raises(grpc.RpcError) as err:
        stub.ListStatements(reasoning_pb2.ListStatementsRequest(), metadata=call_metadata())
    assert err.value.code() != grpc.StatusCode.UNIMPLEMENTED
