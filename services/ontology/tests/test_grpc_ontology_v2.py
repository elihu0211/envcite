"""OntologyService v2：複合自然鍵三欄必填、封閉集合的值才受理，不退回任何預設管轄或方案。"""

from collections.abc import Callable, Iterator

import grpc
import psycopg
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from envcite_ontology import errors, projection
from envcite_ontology.server import build_server
from envcite_ontology.store import OntologyStore
from tests.conftest import call_metadata

pytestmark = pytest.mark.usefixtures("sample")

Stub = ontology_pb2_grpc.OntologyServiceStub


def _identifier(
    jurisdiction: str = "TW-CENTRAL", id_scheme: str = "moj_pcode", local_id: str = "O0020001"
) -> ontology_pb2.LawIdentifier:
    return ontology_pb2.LawIdentifier(
        jurisdiction=jurisdiction, id_scheme=id_scheme, local_id=local_id
    )


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
    stub_v2: Stub,
) -> None:
    law = stub_v2.GetLaw(
        ontology_pb2.GetLawRequest(identifier=_identifier()), metadata=call_metadata()
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
    def test_every_field_must_be_given(self, stub_v2: Stub, call: Callable[..., object]) -> None:
        with pytest.raises(grpc.RpcError) as err:
            call(stub_v2, ontology_pb2.LawIdentifier(local_id="O0020001"))

        assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        reason, violations = _failure(err.value)
        assert reason == errors.INVALID_REQUEST
        assert violations == [
            ("identifier.jurisdiction", errors.EMPTY),
            ("identifier.id_scheme", errors.EMPTY),
        ]

    def test_a_value_outside_the_closed_set_is_invalid_not_missing(
        self, stub_v2: Stub, call: Callable[..., object]
    ) -> None:
        with pytest.raises(grpc.RpcError) as err:
            call(stub_v2, _identifier(jurisdiction="TW-XXX", id_scheme="ly_statute"))

        assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        _, violations = _failure(err.value)
        assert violations == [
            ("identifier.jurisdiction", errors.UNKNOWN_VALUE),
            ("identifier.id_scheme", errors.UNKNOWN_VALUE),
        ]

    def test_an_impossible_pairing_names_no_law(
        self, stub_v2: Stub, call: Callable[..., object]
    ) -> None:
        """中央方案配地方管轄的鍵不可能存在（綱要不收），所以是查無此法，不是改查中央。"""
        with pytest.raises(grpc.RpcError) as err:
            call(stub_v2, _identifier(jurisdiction="TW-TPE"))

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


def test_search_with_an_unknown_jurisdiction_filter_is_invalid(stub_v2: Stub) -> None:
    with pytest.raises(grpc.RpcError) as err:
        stub_v2.SearchArticles(
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
