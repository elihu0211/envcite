from collections.abc import Iterator

import grpc
import pytest

from env_law.consult.ports import Article
from env_law.consult.server import build_server
from env_law.consult.v1 import consult_pb2_grpc

TOKEN = "test-internal-token"


class FakeOntology:
    """An OntologyPort that returns what a test hands it.

    Not a lesser implementation: the pipeline's job is to decide what to do with
    retrieved material, so the retrieval itself is exactly the part a test should
    control.
    """

    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]:
        return self.articles[:limit]


class EchoLlm:
    """An LlmPort that cites every code it is allowed to cite.

    A model that obeys the constraint perfectly. The test for the opposite —
    a model inventing a code — supplies its own fake, because that behaviour is
    what the verification stage exists for.
    """

    def generate(self, *, question: str, evidence: str, allowed_codes: list[str]) -> Iterator[str]:
        yield "依據"
        for code in allowed_codes:
            yield f"[{code}]"
        yield "，本題答覆如上。"


def article(n: int, score: float) -> Article:
    return Article(
        iri=f"https://env.law/eli/tw/law/air-pollution-control-act/art_{n}",
        label=f"空氣污染防制法 第 {n} 條",
        text=f"第 {n} 條條文內容。",
        score=score,
    )


@pytest.fixture
def server_address() -> Iterator[str]:
    server, port = build_server(
        FakeOntology([article(1, 0.9), article(2, 0.4)]), EchoLlm(), TOKEN, "127.0.0.1:0"
    )
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(grace=None)


@pytest.fixture
def stub(server_address: str) -> Iterator[consult_pb2_grpc.ConsultationServiceStub]:
    with grpc.insecure_channel(server_address) as ch:
        yield consult_pb2_grpc.ConsultationServiceStub(ch)


def auth() -> tuple[tuple[str, str], ...]:
    return (
        ("authorization", f"Bearer {TOKEN}"),
        ("x-tenant-id", "acme"),
        ("x-request-id", "req-1"),
    )
