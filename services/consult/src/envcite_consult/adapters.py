"""以真實端點實作的 port。"""

from __future__ import annotations

import grpc

from envcite.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from envcite_consult.ports import Article
from envcite_grpc import downstream_timeout


class GrpcOntology:
    """以 envcite-ontology 的 `SearchArticles` 實作的 `OntologyPort`。"""

    def __init__(self, channel: grpc.Channel, *, timeout: float = 5.0) -> None:
        self._stub = ontology_pb2_grpc.OntologyServiceStub(channel)
        self._timeout = timeout

    def search(
        self, query: str, *, as_of: str = "", limit: int = 10, timeout: float | None = None
    ) -> list[Article]:
        response = self._stub.SearchArticles(
            ontology_pb2.SearchArticlesRequest(query=query, as_of=as_of, limit=limit),
            timeout=downstream_timeout(self._timeout, timeout),
        )
        return [
            Article(
                iri=h.iri,
                label=f"{h.law_title} {_article_label(h.number)}",
                text=h.content,
                # 相關性下界以字面分數或語意相似度校定；分數的定義
                # 一旦改變，下界就必須重新推導。
                score=h.lexical_score,
                lexical_score=h.lexical_score,
                semantic_similarity=(
                    h.semantic_similarity if h.HasField("semantic_similarity") else None
                ),
            )
            for h in response.hits
        ]


def _article_label(number: str) -> str:
    """「20」→ 第 20 條；「5-1」→ 第 5 條之 1。"""
    main, _, sub = number.partition("-")
    return f"第 {main} 條之 {sub}" if sub else f"第 {main} 條"
