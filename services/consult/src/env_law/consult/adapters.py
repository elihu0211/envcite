"""Port implementations over real endpoints.

Only the ontology adapter exists. The language-model one waits on a provider
decision; the pipeline does not care which, which is what `LlmPort` is for.
"""

from __future__ import annotations

import grpc

from env_law.consult.ports import Article
from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc


class GrpcOntology:
    """`OntologyPort` over env-law-ontology's `SearchArticles`."""

    def __init__(self, channel: grpc.Channel, token: str, *, timeout: float = 5.0) -> None:
        self._stub = ontology_pb2_grpc.OntologyServiceStub(channel)
        self._metadata = (("authorization", f"Bearer {token}"),)
        self._timeout = timeout

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]:
        response = self._stub.SearchArticles(
            ontology_pb2.SearchArticlesRequest(query=query, as_of=as_of, limit=limit),
            metadata=self._metadata,
            timeout=self._timeout,
        )
        return [
            Article(
                iri=h.iri,
                label=f"{h.law_title} {_article_label(h.number)}",
                text=h.content,
                # The raw lexical score, never `rank_score`: RRF says nothing about
                # relevance, and `score` feeds the relevance floor (env-law-docs
                # 04-01 §6). While the dense arm is off this is the only raw signal;
                # the floor is conditioned on it and must be re-derived when that
                # changes (§7). A hit without it matched the dense arm alone.
                score=h.lexical_score if h.HasField("lexical_score") else 0.0,
            )
            for h in response.hits
        ]


def _article_label(number: str) -> str:
    """ "20" → 第 20 條; "5-1" → 第 5 條之 1."""
    main, _, sub = number.partition("-")
    return f"第 {main} 條之 {sub}" if sub else f"第 {main} 條"
