"""OntologyService：複合自然鍵三欄必填、封閉集合的值才受理，不退回任何預設管轄或方案。"""

import os
import signal
from collections.abc import Callable, Iterator

import grpc
import psycopg
import pytest
from google.rpc import error_details_pb2
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_status import rpc_status

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from envcite_grpc import stop_on_sigterm
from envcite_ontology import errors, projection
from envcite_ontology.ruling import load
from envcite_ontology.server import build_server
from envcite_ontology.store import OntologyStore

from .conftest import call_metadata, identifier

pytestmark = pytest.mark.usefixtures("sample")

Stub = ontology_pb2_grpc.OntologyServiceStub


def _failure(error: grpc.RpcError) -> tuple[str, list[tuple[str, str]]]:
    status = rpc_status.from_call(error)  # type: ignore[arg-type]
    assert status is not None
    reason = ""
    violations: list[tuple[str, str]] = []
    for detail in status.details:
        if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
            info = error_details_pb2.ErrorInfo()
            detail.Unpack(info)
            reason = info.reason
        elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
            bad = error_details_pb2.BadRequest()
            detail.Unpack(bad)
            violations = [(v.field, v.reason) for v in bad.field_violations]
    return reason, violations


def test_get_law_returns_the_natural_key_and_nothing_else_the_corpus_does_not_hold(
    stub: Stub,
) -> None:
    law = stub.GetLaw(
        ontology_pb2.GetLawRequest(identifier=identifier("O0020001")), metadata=call_metadata()
    ).law

    assert (law.identifier.jurisdiction, law.identifier.id_scheme, law.identifier.local_id) == (
        "TW-CENTRAL",
        "moj_pcode",
        "O0020001",
    )
    assert law.title == "空氣污染防制法"
    assert {f.name for f in law.DESCRIPTOR.fields} == {
        "identifier",
        "iri",
        "title",
        "language",
        "date_document",
    }


@pytest.mark.parametrize(
    "call",
    [
        lambda s, i: s.GetLaw(ontology_pb2.GetLawRequest(identifier=i), metadata=call_metadata()),
        lambda s, i: s.ListArticles(
            ontology_pb2.ListArticlesRequest(identifier=i), metadata=call_metadata()
        ),
        lambda s, i: s.ListInvalidations(
            ontology_pb2.ListInvalidationsRequest(identifier=i), metadata=call_metadata()
        ),
    ],
    ids=["GetLaw", "ListArticles", "ListInvalidations"],
)
class TestIdentifierIsRequired:
    def test_every_field_must_be_given(self, stub: Stub, call: Callable[..., object]) -> None:
        with pytest.raises(grpc.RpcError) as err:
            call(stub, ontology_pb2.LawIdentifier(local_id="O0020001"))

        assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        reason, violations = _failure(err.value)
        assert reason == errors.INVALID_REQUEST
        assert violations == [
            ("identifier.jurisdiction", errors.EMPTY),
            ("identifier.id_scheme", errors.EMPTY),
        ]

    def test_a_value_outside_the_closed_set_is_invalid_not_missing(
        self, stub: Stub, call: Callable[..., object]
    ) -> None:
        with pytest.raises(grpc.RpcError) as err:
            call(stub, identifier("O0020001", jurisdiction="TW-XXX", id_scheme="ly_statute"))

        assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        _, violations = _failure(err.value)
        assert violations == [
            ("identifier.jurisdiction", errors.UNKNOWN_VALUE),
            ("identifier.id_scheme", errors.UNKNOWN_VALUE),
        ]

    def test_an_impossible_pairing_names_no_law(
        self, stub: Stub, call: Callable[..., object]
    ) -> None:
        """中央方案配地方管轄的鍵不可能存在（綱要不收），所以是查無此法，不是改查中央。"""
        with pytest.raises(grpc.RpcError) as err:
            call(stub, identifier("O0020001", jurisdiction="TW-TPE"))

        assert err.value.code() == grpc.StatusCode.NOT_FOUND
        reason, _ = _failure(err.value)
        assert reason == errors.LAW_NOT_FOUND


@pytest.fixture
def search_stub(
    conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore
) -> Iterator[Stub]:
    """詞典啟動時讀一次，所以伺服器要在匯入與建索引之後才起。"""
    assert projection.rebuild(conn) > 0
    conn.commit()
    server, port = build_server(store, "127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield ontology_pb2_grpc.OntologyServiceStub(channel)
    server.stop(grace=None)


def test_search_with_an_unknown_jurisdiction_filter_is_invalid(stub: Stub) -> None:
    with pytest.raises(grpc.RpcError) as err:
        stub.SearchArticles(
            ontology_pb2.SearchArticlesRequest(query="空氣污染", jurisdiction="TW-XXX"),
            metadata=call_metadata(),
        )

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    _, violations = _failure(err.value)
    assert violations == [("jurisdiction", errors.UNKNOWN_VALUE)]


def test_search_without_a_jurisdiction_filter_looks_everywhere_and_a_filter_narrows(
    search_stub: Stub,
) -> None:
    everywhere = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="空氣污染"), metadata=call_metadata()
    )
    central = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="空氣污染", jurisdiction="TW-CENTRAL"),
        metadata=call_metadata(),
    )
    taipei = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="空氣污染", jurisdiction="TW-TPE"),
        metadata=call_metadata(),
    )

    assert list(everywhere.hits) == list(central.hits)
    assert everywhere.hits
    assert list(taipei.hits) == []


def test_list_articles(stub: Stub) -> None:
    response = stub.ListArticles(
        ontology_pb2.ListArticlesRequest(identifier=identifier("O0020001")),
        metadata=call_metadata(),
    )

    assert [a.number for a in response.articles] == ["1", "100"]


def test_unknown_law_is_not_found(stub: Stub) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.GetLaw(
            ontology_pb2.GetLawRequest(identifier=identifier("Z9999999")),
            metadata=call_metadata(),
        )

    assert error.value.code() == grpc.StatusCode.NOT_FOUND
    assert _failure(error.value)[0] == errors.LAW_NOT_FOUND


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
    stub: Stub, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    _ruling_on_air_article_1(conn)

    def ask(as_of: str, article: str = "") -> ontology_pb2.ListInvalidationsResponse:
        return stub.ListInvalidations(
            ontology_pb2.ListInvalidationsRequest(
                identifier=identifier("O0020001"), as_of=as_of, article_number=article
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
    stub: Stub,
) -> None:
    empty = stub.ListInvalidations(
        ontology_pb2.ListInvalidationsRequest(identifier=identifier("O0020001")),
        metadata=call_metadata(),
    )
    assert list(empty.invalidations) == []
    with pytest.raises(grpc.RpcError) as error:
        stub.ListInvalidations(
            ontology_pb2.ListInvalidationsRequest(identifier=identifier("Z9999999")),
            metadata=call_metadata(),
        )
    assert error.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize(
    "service", ["envcite.ontology.v2.OntologyService", "envcite.ontology.v2.ReasoningService"]
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
