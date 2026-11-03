import os
import signal

import grpc
import psycopg
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from envcite.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from envcite_grpc import stop_on_sigterm
from envcite_ontology.ruling import load
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


def _ruling_on_air_article_1(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    load(
        conn,
        {
            "rulings": [
                {
                    "ruling_no": "釋字第1號",
                    "kind": "interpretation",
                    "decided_on": "2019-01-01",
                    "source_url": "https://cons.judicial.gov.tw/docdata.aspx?fid=100&id=1",
                    "excerpt": "空氣污染防制法第1條違憲，至遲於屆滿一年時失其效力。",
                    "invalidates": [
                        {
                            "law": "O0020001",
                            "path": "art_1",
                            "invalid_from": "2020-01-01",
                            "partial": True,
                        }
                    ],
                }
            ]
        },
    )


def test_list_invalidations_answers_by_the_date_asked(
    stub: ontology_pb2_grpc.OntologyServiceStub, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    _ruling_on_air_article_1(conn)

    def ask(as_of: str, article: str = "") -> ontology_pb2.ListInvalidationsResponse:
        return stub.ListInvalidations(
            ontology_pb2.ListInvalidationsRequest(
                law_id="O0020001", as_of=as_of, article_number=article
            ),
            metadata=call_metadata(),
        )

    assert list(ask("2019-12-31").invalidations) == []
    (found,) = ask("2020-01-01").invalidations
    assert (found.ruling_no, found.path, found.invalid_from, found.partial) == (
        "釋字第1號",
        "art_1",
        "2020-01-01",
        True,
    )
    assert found.ruling_kind == ontology_pb2.RULING_KIND_INTERPRETATION
    assert found.source_url.startswith("https://cons.judicial.gov.tw/")
    assert "失其效力" in found.excerpt
    assert len(ask("2020-01-01", article="1").invalidations) == 1
    assert list(ask("2020-01-01", article="100").invalidations) == []


def test_list_invalidations_without_a_ruling_is_empty_and_unknown_law_is_not_found(
    stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    empty = stub.ListInvalidations(
        ontology_pb2.ListInvalidationsRequest(law_id="O0020001"), metadata=call_metadata()
    )
    assert list(empty.invalidations) == []
    with pytest.raises(grpc.RpcError) as error:
        stub.ListInvalidations(
            ontology_pb2.ListInvalidationsRequest(law_id="Z9999999"), metadata=call_metadata()
        )
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
