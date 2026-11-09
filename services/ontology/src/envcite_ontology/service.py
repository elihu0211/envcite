"""gRPC servicer。

錯誤一律丟 `errors.ServiceError`，由 `errors.maps_errors` 轉成帶 ErrorInfo 的狀態。
"""

from __future__ import annotations

import logging

import grpc

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite.ontology.v2 import ontology_pb2_grpc
from envcite_ontology import errors
from envcite_ontology.embedding import Embedder, EmbeddingError
from envcite_ontology.identifiers import jurisdiction_filter, law_key
from envcite_ontology.law_names import LawNames
from envcite_ontology.store import LawKey, OntologyStore
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)


#: `limit` 為 0 時的預設命中數，與可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50

_RULING_KINDS = {
    "interpretation": pb.RULING_KIND_INTERPRETATION,
    "judgment": pb.RULING_KIND_JUDGMENT,
}


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    def __init__(
        self,
        store: OntologyStore,
        segmenter: Segmenter,
        law_names: LawNames,
        embedder: Embedder | None = None,
        lexical_max_df: float = 0.0,
    ) -> None:
        self._store = store
        self._law_names = law_names
        self._lexical_max_df = lexical_max_df
        self._segmenter = segmenter
        self._embedder = embedder

    def _not_found(self, key: LawKey, as_of: str) -> errors.ServiceError:
        if self._store.law_exists(key):
            when = f"in force on {as_of}" if as_of else "in force today"
            return errors.not_found(errors.AS_OF_NOT_COVERED, f"law '{key}' has no version {when}")
        return errors.not_found(errors.LAW_NOT_FOUND, f"law '{key}' not found")

    def _request_key(self, identifier: pb.LawIdentifier, as_of: str) -> LawKey:
        """必填的自然鍵與 `as_of` 一次驗證，違規一次回報。"""
        v = errors.Validator()
        key = law_key(self._store, identifier, v)
        v.date("as_of", as_of)
        v.check()
        return key

    @errors.maps_errors
    def GetLaw(self, request: pb.GetLawRequest, context: grpc.ServicerContext) -> pb.GetLawResponse:
        _log_call(context, "GetLaw", _identifier_subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        law = self._store.get_law(key, as_of=request.as_of)
        if law is None:
            raise self._not_found(key, request.as_of)
        return pb.GetLawResponse(
            law=pb.Law(
                identifier=pb.LawIdentifier(
                    jurisdiction=law.jurisdiction,
                    id_scheme=law.id_scheme,
                    local_id=law.local_id,
                ),
                iri=law.iri,
                title=law.title,
                language=law.language,
                date_document=law.date_document,
            )
        )

    @errors.maps_errors
    def ListArticles(
        self, request: pb.ListArticlesRequest, context: grpc.ServicerContext
    ) -> pb.ListArticlesResponse:
        _log_call(context, "ListArticles", _identifier_subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        articles = self._store.list_articles(key, as_of=request.as_of)
        if articles is None:
            raise self._not_found(key, request.as_of)
        return pb.ListArticlesResponse(
            articles=[pb.Article(iri=a.iri, number=a.number) for a in articles]
        )

    @errors.maps_errors
    def ListInvalidations(
        self, request: pb.ListInvalidationsRequest, context: grpc.ServicerContext
    ) -> pb.ListInvalidationsResponse:
        _log_call(context, "ListInvalidations", _identifier_subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        found = self._store.list_invalidations(
            key, as_of=request.as_of, article_number=request.article_number
        )
        if found is None:
            raise self._not_found(key, request.as_of)
        return pb.ListInvalidationsResponse(
            invalidations=[
                pb.Invalidation(
                    ruling_no=i.ruling_no,
                    ruling_kind=_RULING_KINDS[i.ruling_kind],
                    decided_on=i.decided_on,
                    source_url=i.source_url,
                    excerpt=i.excerpt,
                    path=i.path,
                    invalid_from=i.invalid_from,
                    partial=i.partial,
                )
                for i in found
            ]
        )

    @errors.maps_errors
    def SearchArticles(
        self, request: pb.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> pb.SearchArticlesResponse:
        _log_call(context, "SearchArticles", f"query_chars={len(request.query)}")
        v = errors.Validator()
        terms = self._segmenter.to_tsvector_input(request.query)
        if not terms:
            v.add("query", errors.EMPTY, "query must not be empty")
        limit = request.limit or DEFAULT_SEARCH_LIMIT
        if not 0 < limit <= MAX_SEARCH_LIMIT:
            v.add("limit", errors.OUT_OF_RANGE, f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        v.date("as_of", request.as_of)
        jurisdiction = jurisdiction_filter(self._store, request.jurisdiction, v)
        v.check()

        hits = self._store.search(
            terms,
            tokenizer=self._segmenter.tokenizer_id,
            as_of=request.as_of,
            limit=limit,
            embedding=self._query_embedding(request.query),
            model=self._embedder.model if self._embedder else "",
            max_df=self._lexical_max_df,
            laws=self._law_names.find(request.query),
            jurisdiction=jurisdiction,
        )
        return pb.SearchArticlesResponse(
            hits=[
                pb.ArticleHit(
                    iri=h.iri,
                    identifier=pb.LawIdentifier(
                        jurisdiction=h.jurisdiction,
                        id_scheme=h.id_scheme,
                        local_id=h.local_id,
                    ),
                    law_title=h.law_title,
                    number=h.number,
                    content=h.content,
                    lexical_score=h.lexical_score,
                    semantic_similarity=h.semantic_similarity,
                )
                for h in hits
            ]
        )

    def _query_embedding(self, query: str) -> list[float] | None:
        if self._embedder is None:
            return None
        try:
            return self._embedder.embed([query])[0]
        except EmbeddingError as e:
            log.warning("semantic arm skipped: %s", e)
            return None


def _identifier_subject(identifier: pb.LawIdentifier) -> str:
    return f"{identifier.jurisdiction}:{identifier.id_scheme}:{identifier.local_id}"


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
