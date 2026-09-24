from collections.abc import Iterator
from concurrent import futures

import grpc
import pytest

from env_law.consult.adapters import GrpcOntology
from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc


class _Ontology(ontology_pb2_grpc.OntologyServiceServicer):
    """Answers SearchArticles with fixed hits and records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[tuple[ontology_pb2.SearchArticlesRequest, dict[str, str]]] = []

    def SearchArticles(
        self, request: ontology_pb2.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.SearchArticlesResponse:
        self.requests.append((request, dict(context.invocation_metadata())))  # type: ignore[arg-type]
        return ontology_pb2.SearchArticlesResponse(
            hits=[
                ontology_pb2.ArticleHit(
                    iri="a",
                    law_title="空氣污染防制法",
                    number="5-1",
                    content="…",
                    rank_score=0.03,
                    lexical_score=0.4,
                ),
                ontology_pb2.ArticleHit(
                    iri="b",
                    law_title="空氣污染防制法",
                    number="20",
                    content="…",
                    rank_score=0.02,
                    dense_distance=0.2,
                ),
            ]
        )


@pytest.fixture
def ontology() -> Iterator[tuple[_Ontology, GrpcOntology]]:
    fake = _Ontology()  # type: ignore[abstract]  # only SearchArticles is exercised
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    ontology_pb2_grpc.add_OntologyServiceServicer_to_server(fake, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield fake, GrpcOntology(channel, "secret")
    server.stop(grace=None)


def test_hits_become_articles_scored_by_the_raw_lexical_signal(
    ontology: tuple[_Ontology, GrpcOntology],
) -> None:
    _, adapter = ontology

    articles = adapter.search("排放標準")

    assert [(a.iri, a.label, a.score) for a in articles] == [
        ("a", "空氣污染防制法 第 5 條之 1", pytest.approx(0.4)),
        # Dense-only hit: no lexical signal, so nothing clears the floor on its behalf.
        ("b", "空氣污染防制法 第 20 條", 0.0),
    ]


def test_search_sends_the_internal_token_and_arguments(
    ontology: tuple[_Ontology, GrpcOntology],
) -> None:
    fake, adapter = ontology

    adapter.search("排放", as_of="2024-01-01", limit=3)

    request, metadata = fake.requests[0]
    assert (request.query, request.as_of, request.limit) == ("排放", "2024-01-01", 3)
    assert metadata["authorization"] == "Bearer secret"
