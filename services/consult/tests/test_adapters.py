from collections.abc import Iterator
from concurrent import futures

import grpc
import pytest

from envcite.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from envcite_consult.adapters import GrpcOntology


class _Ontology(ontology_pb2_grpc.OntologyServiceServicer):
    """Answers SearchArticles with fixed hits and records what it was sent."""

    def __init__(self) -> None:
        self.requests: list[ontology_pb2.SearchArticlesRequest] = []

    def SearchArticles(
        self, request: ontology_pb2.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.SearchArticlesResponse:
        self.requests.append(request)
        return ontology_pb2.SearchArticlesResponse(
            hits=[
                ontology_pb2.ArticleHit(
                    iri="a",
                    law_title="空氣污染防制法",
                    number="5-1",
                    content="…",
                    lexical_score=0.4,
                    semantic_similarity=0.85,
                ),
                ontology_pb2.ArticleHit(
                    iri="b",
                    law_title="空氣污染防制法",
                    number="20",
                    content="…",
                    lexical_score=0.1,
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
        yield fake, GrpcOntology(channel)
    server.stop(grace=None)


def test_hits_become_articles_scored_by_lexical_relevance(
    ontology: tuple[_Ontology, GrpcOntology],
) -> None:
    _, adapter = ontology

    articles = adapter.search("排放標準")

    assert [(a.iri, a.label) for a in articles] == [
        ("a", "空氣污染防制法 第 5 條之 1"),
        ("b", "空氣污染防制法 第 20 條"),
    ]
    assert [a.score for a in articles] == pytest.approx([0.4, 0.1])
    assert articles[0].semantic_similarity == pytest.approx(0.85)
    assert articles[1].semantic_similarity is None


def test_search_sends_the_arguments(
    ontology: tuple[_Ontology, GrpcOntology],
) -> None:
    fake, adapter = ontology

    adapter.search("排放", as_of="2024-01-01", limit=3)

    request = fake.requests[0]
    assert (request.query, request.as_of, request.limit) == ("排放", "2024-01-01", 3)
