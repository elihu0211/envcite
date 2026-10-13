"""gRPC servicer。內部 token 認證為共用元件（`env_law_grpc.InternalTokenInterceptor`）。

錯誤一律丟 `errors.ServiceError`，由 `errors.maps_errors` 轉成帶 ErrorInfo 的狀態。
"""

from __future__ import annotations

import logging

import grpc

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law_ontology import errors
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)


#: `limit` 為 0 時的預設命中數，與可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    def __init__(self, store: OntologyStore, segmenter: Segmenter) -> None:
        self._store = store
        self._segmenter = segmenter

    def _not_found(self, law_id: str, as_of: str) -> errors.ServiceError:
        # 法規不存在與該日無施行版本，契約都回 NOT_FOUND，reason 分開：呼叫端要能分辨語料缺漏
        # 與「那天沒有這部法」。
        if self._store.law_exists(law_id):
            when = f"in force on {as_of}" if as_of else "in force today"
            return errors.not_found(
                errors.AS_OF_NOT_COVERED, f"law '{law_id}' has no version {when}"
            )
        return errors.not_found(errors.LAW_NOT_FOUND, f"law '{law_id}' not found")

    @errors.maps_errors
    def GetLaw(
        self, request: ontology_pb2.GetLawRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.GetLawResponse:
        _log_call(context, "GetLaw", request.id)
        _check_as_of(request.as_of)
        law = self._store.get_law(request.id, as_of=request.as_of)
        if law is None:
            raise self._not_found(request.id, request.as_of)
        return ontology_pb2.GetLawResponse(
            law=ontology_pb2.Law(
                id=law.id,
                iri=law.iri,
                title=law.title,
                language=law.language,
                date_document=law.date_document,
            )
        )

    @errors.maps_errors
    def ListArticles(
        self, request: ontology_pb2.ListArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.ListArticlesResponse:
        _log_call(context, "ListArticles", request.law_id)
        _check_as_of(request.as_of)
        articles = self._store.list_articles(request.law_id, as_of=request.as_of)
        if articles is None:
            raise self._not_found(request.law_id, request.as_of)
        return ontology_pb2.ListArticlesResponse(
            articles=[ontology_pb2.Article(iri=a.iri, number=a.number) for a in articles]
        )

    @errors.maps_errors
    def SearchArticles(
        self, request: ontology_pb2.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.SearchArticlesResponse:
        # 查詢文字可能是客戶案件內容：日誌只記長度。
        _log_call(context, "SearchArticles", f"query_chars={len(request.query)}")
        v = errors.Validator()
        terms = self._segmenter.to_tsvector_input(request.query)
        if not terms:
            v.add("query", errors.EMPTY, "query must not be empty")
        limit = request.limit or DEFAULT_SEARCH_LIMIT
        if not 0 < limit <= MAX_SEARCH_LIMIT:
            v.add("limit", errors.OUT_OF_RANGE, f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        v.date("as_of", request.as_of)
        v.check()

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


def _check_as_of(as_of: str) -> None:
    v = errors.Validator()
    v.date("as_of", as_of)
    v.check()


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
