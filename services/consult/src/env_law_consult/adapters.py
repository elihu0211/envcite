"""以真實端點實作的 port。"""

from __future__ import annotations

import grpc

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law_consult.ports import Article


class GrpcOntology:
    """以 env-law-ontology 的 `SearchArticles` 實作的 `OntologyPort`。"""

    def __init__(self, channel: grpc.Channel, *, timeout: float = 5.0) -> None:
        self._stub = ontology_pb2_grpc.OntologyServiceStub(channel)
        self._timeout = timeout

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]:
        response = self._stub.SearchArticles(
            ontology_pb2.SearchArticlesRequest(query=query, as_of=as_of, limit=limit),
            timeout=self._timeout,
        )
        return [
            Article(
                iri=h.iri,
                label=f"{h.law_title} {_article_label(h.number)}",
                text=h.content,
                # 相關性下界以這個分數校定（env-law-docs 04-01 §6、§7）；分數的定義
                # 一旦改變，下界就必須重新推導。
                score=h.lexical_score,
            )
            for h in response.hits
        ]


def _article_label(number: str) -> str:
    """「20」→ 第 20 條；「5-1」→ 第 5 條之 1。"""
    main, _, sub = number.partition("-")
    return f"第 {main} 條之 {sub}" if sub else f"第 {main} 條"
