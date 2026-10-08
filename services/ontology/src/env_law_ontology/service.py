"""gRPC servicer。內部 token 認證為共用元件（`env_law_grpc.InternalTokenInterceptor`）。"""

from __future__ import annotations

import logging
from datetime import date

import grpc

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)


#: 呼叫端將 `limit` 留為 0 時回傳的命中數，以及可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    def __init__(self, store: OntologyStore, segmenter: Segmenter) -> None:
        self._store = store
        self._segmenter = segmenter

    def GetLaw(
        self, request: ontology_pb2.GetLawRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.GetLawResponse:
        _log_call(context, "GetLaw", request.id)
        _check_as_of(context, request.as_of)
        law = self._store.get_law(request.id, as_of=request.as_of)
        if law is None:
            context.abort(grpc.StatusCode.NOT_FOUND, _not_found(request.id, request.as_of))
        return ontology_pb2.GetLawResponse(
            law=ontology_pb2.Law(
                id=law.id,
                iri=law.iri,
                title=law.title,
                language=law.language,
                date_document=law.date_document,
            )
        )

    def ListArticles(
        self, request: ontology_pb2.ListArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.ListArticlesResponse:
        _log_call(context, "ListArticles", request.law_id)
        _check_as_of(context, request.as_of)
        articles = self._store.list_articles(request.law_id, as_of=request.as_of)
        if articles is None:
            context.abort(grpc.StatusCode.NOT_FOUND, _not_found(request.law_id, request.as_of))
        return ontology_pb2.ListArticlesResponse(
            articles=[ontology_pb2.Article(iri=a.iri, number=a.number) for a in articles]
        )

    def SearchArticles(
        self, request: ontology_pb2.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.SearchArticlesResponse:
        # 查詢全文是客戶的案件內容（顧問查的往往是客戶的案子）：日誌只記長度，不記文字。
        _log_call(context, "SearchArticles", f"query_chars={len(request.query)}")
        terms = self._segmenter.to_tsvector_input(request.query)
        if not terms:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "query must not be empty")
        limit = request.limit or DEFAULT_SEARCH_LIMIT
        if not 0 < limit <= MAX_SEARCH_LIMIT:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, f"limit must be between 1 and {MAX_SEARCH_LIMIT}"
            )
        _check_as_of(context, request.as_of)

        hits = self._store.search(
            terms, tokenizer=self._segmenter.tokenizer_id, as_of=request.as_of, limit=limit
        )
        return ontology_pb2.SearchArticlesResponse(
            hits=[
                ontology_pb2.ArticleHit(
                    iri=h.iri,
                    law_id=h.law_id,
                    law_title=h.law_title,
                    number=h.number,
                    content=h.content,
                    lexical_score=h.lexical_score,
                )
                for h in hits
            ]
        )


def _check_as_of(context: grpc.ServicerContext, as_of: str) -> None:
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "as_of must be an ISO 8601 date")


def _not_found(law_id: str, as_of: str) -> str:
    # 法規不存在與該日無施行版本，契約都回 NOT_FOUND；帶上日期，訊息才說得出查的是哪一版。
    return f"law '{law_id}' not found" + (f" in force on {as_of}" if as_of else "")


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
