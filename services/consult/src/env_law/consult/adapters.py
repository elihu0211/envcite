"""以真實端點實作的 port。

目前只有本體 adapter。語言模型的 adapter 等待供應商的決定；管線不在乎是哪一家，
這正是 `LlmPort` 存在的目的。
"""

from __future__ import annotations

import grpc

from env_law.consult.ports import Article
from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc


class GrpcOntology:
    """以 env-law-ontology 的 `SearchArticles` 實作的 `OntologyPort`。"""

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
                # 用原始的字面分數，絕不用 `rank_score`：RRF 不反映相關性，而 `score`
                # 會餵給相關性下界（env-law-docs 04-01 §6）。稠密檢索臂關閉期間，這是
                # 唯一的原始訊號；下界以它為條件校定，它一旦改變，下界就必須重新推導
                # （§7）。沒有此分數的命中結果，是僅由稠密檢索臂比對到的。
                score=h.lexical_score if h.HasField("lexical_score") else 0.0,
            )
            for h in response.hits
        ]


def _article_label(number: str) -> str:
    """「20」→ 第 20 條；「5-1」→ 第 5 條之 1。"""
    main, _, sub = number.partition("-")
    return f"第 {main} 條之 {sub}" if sub else f"第 {main} 條"
