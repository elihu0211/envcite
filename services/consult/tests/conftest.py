from collections.abc import Iterator

import grpc
import pytest

from env_law.consult.v2 import consult_pb2_grpc
from env_law_consult.ports import Article
from env_law_consult.server import build_server


class FakeOntology:
    """An OntologyPort that returns what a test hands it.

    Not a lesser implementation: the pipeline's job is to decide what to do with
    retrieved material, so the retrieval itself is exactly the part a test should
    control.
    """

    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles
        self.searches: list[tuple[str, str]] = []

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]:
        self.searches.append((query, as_of))
        return self.articles[:limit]


def article(n: int, score: float) -> Article:
    return Article(
        iri=f"https://env.law/tw/law/O0020001/2018-08-01/zho/art_{n}",
        label=f"空氣污染防制法 第 {n} 條",
        text=f"第 {n} 條條文內容。",
        score=score,
    )


@pytest.fixture
def ontology() -> FakeOntology:
    return FakeOntology([article(1, 0.9), article(2, 0.4)])


@pytest.fixture
def server_address(ontology: FakeOntology) -> Iterator[str]:
    server, port = build_server(ontology, "127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(grace=None)


@pytest.fixture
def stub(server_address: str) -> Iterator[consult_pb2_grpc.ConsultationServiceStub]:
    with grpc.insecure_channel(server_address) as ch:
        yield consult_pb2_grpc.ConsultationServiceStub(ch)


def call_metadata() -> tuple[tuple[str, str], ...]:
    return (
        ("x-tenant-id", "acme"),
        ("x-request-id", "req-1"),
    )
