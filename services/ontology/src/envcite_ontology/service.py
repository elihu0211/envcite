"""gRPC servicer。

錯誤一律丟 `errors.ServiceError`，由 `errors.maps_errors` 轉成帶 ErrorInfo 的狀態。
"""

import logging
import threading

import grpc

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite.ontology.v2 import ontology_pb2_grpc
from envcite_grpc import remaining_seconds
from envcite_ontology import errors, identifiers
from envcite_ontology.coverage import term_coverage
from envcite_ontology.embedding import Embedder, EmbeddingError
from envcite_ontology.guidance_search import CANDIDATES as GUIDANCE_CANDIDATES
from envcite_ontology.guidance_search import EXTRA as GUIDANCE_EXTRA
from envcite_ontology.guidance_search import fuse, guidance_phrases
from envcite_ontology.identifiers import jurisdiction_filter, law_key
from envcite_ontology.law_names import LawNames
from envcite_ontology.reranking import RerankError, Reranks, select
from envcite_ontology.store import LawKey, OntologyStore, SearchHit
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)


#: `limit` 為 0 時的預設命中數，與可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
#: 請求帶 `rerank` 時，兩臂預設取幾條候選交給精排模型（`ServerSettings.rerank_candidates` 覆寫）。
DEFAULT_RERANK_CANDIDATES = 30

_RULING_KINDS = {
    "interpretation": pb.RULING_KIND_INTERPRETATION,
    "judgment": pb.RULING_KIND_JUDGMENT,
}

_HEADING_KINDS = {
    "part": pb.HEADING_KIND_PART,
    "chapter": pb.HEADING_KIND_CHAPTER,
    "section": pb.HEADING_KIND_SECTION,
    "subsection": pb.HEADING_KIND_SUBSECTION,
    "item_heading": pb.HEADING_KIND_ITEM_HEADING,
}


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    """`OntologyService` 的 servicer：取法規、列條文與失效條文、混合檢索條文。"""

    def __init__(
        self,
        store: OntologyStore,
        segmenter: Segmenter,
        law_names: LawNames,
        embedder: Embedder | None = None,
        lexical_max_df: float = 0.0,
        embed_slots: int | None = None,
        guidance_max_df: float = 0.0,
        reranker: Reranks | None = None,
        rerank_slots: int | None = None,
        rerank_candidates: int = DEFAULT_RERANK_CANDIDATES,
    ) -> None:
        self._store = store
        self._law_names = law_names
        self._lexical_max_df = lexical_max_df
        self._guidance_max_df = guidance_max_df
        self._segmenter = segmenter
        self._embedder = embedder
        # 名額滿了就略過語意臂。None 表示不限（測試直接建構、不走伺服器預算時）。
        self._embed_slots = (
            threading.BoundedSemaphore(embed_slots) if embed_slots is not None else None
        )
        self._reranker = reranker
        self._rerank_candidates = rerank_candidates
        self._rerank_slots = (
            threading.BoundedSemaphore(rerank_slots) if rerank_slots is not None else None
        )

    def _request_key(self, identifier: pb.LawIdentifier, as_of: str) -> LawKey:
        """必填的自然鍵與 `as_of` 一次驗證，違規一次回報。"""
        v = errors.Validator()
        key = law_key(self._store, identifier, v)
        v.date("as_of", as_of)
        v.check()
        return key

    @errors.maps_errors
    def GetLaw(self, request: pb.GetLawRequest, context: grpc.ServicerContext) -> pb.GetLawResponse:
        _log_call(context, "GetLaw", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        law = self._store.get_law(key, as_of=request.as_of, timeout=remaining_seconds(context))
        if law is None:
            raise identifiers.not_found(self._store, key, request.as_of, context)
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
        _log_call(context, "ListArticles", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        timeout = remaining_seconds(context)
        articles = self._store.list_articles(key, as_of=request.as_of, timeout=timeout)
        if articles is None:
            raise identifiers.not_found(self._store, key, request.as_of, context)
        headings = self._store.list_headings(key, as_of=request.as_of, timeout=timeout) or []
        return pb.ListArticlesResponse(
            articles=[
                pb.Article(iri=a.iri, number=a.number, sort_key=a.sort_key) for a in articles
            ],
            headings=[
                pb.StructureHeading(
                    kind=_HEADING_KINDS.get(h.kind, pb.HEADING_KIND_UNSPECIFIED),
                    heading_no=h.heading_no,
                    title=h.title,
                    sort_key=h.sort_key,
                    parent_index=h.parent_index,
                )
                for h in headings
            ],
        )

    @errors.maps_errors
    def ListLaws(
        self, request: pb.ListLawsRequest, context: grpc.ServicerContext
    ) -> pb.ListLawsResponse:
        _log_call(context, "ListLaws", request.jurisdiction or "all")
        v = errors.Validator()
        v.date("as_of", request.as_of)
        jurisdiction = jurisdiction_filter(self._store, request.jurisdiction, v)
        v.check()

        laws = self._store.list_laws(
            as_of=request.as_of,
            jurisdiction=jurisdiction,
            timeout=remaining_seconds(context),
        )
        items: list[pb.LawSummary] = []
        for item in laws:
            parent = (
                pb.LawIdentifier(
                    jurisdiction=item.parent_jurisdiction,
                    id_scheme=item.parent_id_scheme,
                    local_id=item.parent_local_id,
                )
                if item.parent_jurisdiction and item.parent_id_scheme and item.parent_local_id
                else None
            )
            items.append(
                pb.LawSummary(
                    identifier=pb.LawIdentifier(
                        jurisdiction=item.jurisdiction,
                        id_scheme=item.id_scheme,
                        local_id=item.local_id,
                    ),
                    iri=item.iri,
                    title=item.title,
                    norm_kind=item.norm_kind,
                    date_document=item.date_document,
                    parent_identifier=parent,
                )
            )
        return pb.ListLawsResponse(laws=items)

    @errors.maps_errors
    def ListInvalidations(
        self, request: pb.ListInvalidationsRequest, context: grpc.ServicerContext
    ) -> pb.ListInvalidationsResponse:
        _log_call(context, "ListInvalidations", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        found = self._store.list_invalidations(
            key,
            as_of=request.as_of,
            article_number=request.article_number,
            timeout=remaining_seconds(context),
        )
        if found is None:
            raise identifiers.not_found(self._store, key, request.as_of, context)
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

        # 精排要看得比 limit 深：取較深的候選，精排後再取前 limit 條。
        # 沒有精排模型時，請求的 rerank 當作沒有。
        rerank = request.rerank and self._reranker is not None
        depth = max(limit, self._rerank_candidates) if rerank else limit
        hits = self._store.search(
            terms,
            tokenizer=self._segmenter.tokenizer_id,
            as_of=request.as_of,
            limit=depth,
            embedding=self._query_embedding(request.query),
            model=self._embedder.model if self._embedder else "",
            max_df=self._lexical_max_df,
            laws=self._law_names.find(request.query),
            jurisdiction=jurisdiction,
            timeout=remaining_seconds(context),
        )
        searched = self._store.searched_terms(
            terms,
            tokenizer=self._segmenter.tokenizer_id,
            max_df=self._lexical_max_df,
            timeout=remaining_seconds(context),
        )
        ranked = [(h, 0) for h in hits]
        if request.include_guidance and (phrases := guidance_phrases(request.query)):
            ranked = fuse(
                hits,
                self._store.guidance_hits(
                    phrases,
                    as_of=request.as_of,
                    limit=GUIDANCE_CANDIDATES,
                    jurisdiction=jurisdiction,
                    max_df=self._guidance_max_df,
                    timeout=remaining_seconds(context),
                ),
                depth,
            )
        scored: list[tuple[tuple[SearchHit, int], float | None]] = [(r, None) for r in ranked]
        if rerank:
            scored = self._rerank(request.query, ranked, limit, remaining_seconds(context))
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
                    term_coverage=term_coverage(searched, h.content, self._segmenter),
                    guidance_documents=documents,
                    rerank_score=score,
                )
                for (h, documents), score in scored
            ]
        )

    def _rerank(
        self,
        query: str,
        ranked: list[tuple[SearchHit, int]],
        limit: int,
        timeout: float | None,
    ) -> list[tuple[tuple[SearchHit, int], float | None]]:
        """精排後的前 `limit` 條（加上函釋帶出而被擠出去的，最多 `GUIDANCE_EXTRA` 條）與各自的分數。

        精排失敗（名額滿、逾時、端點錯誤、回應不合）時退回精排前的次序，分數不設：精排是加強，
        不是查詢成功的條件。退回時一樣只取前 `limit` 條，所以呼叫端拿到的條數與沒要精排時相同。
        """
        scores = self._rerank_scores(query, [h.content for h, _ in ranked], timeout)
        if scores is None:
            kept = select(
                ranked,
                [-float(i) for i in range(len(ranked))],
                limit=limit,
                extra=GUIDANCE_EXTRA,
                is_extra=_guided,
            )
            return [(item, None) for item, _ in kept]
        return list(select(ranked, scores, limit=limit, extra=GUIDANCE_EXTRA, is_extra=_guided))

    def _rerank_scores(
        self, query: str, documents: list[str], timeout: float | None
    ) -> list[float] | None:
        reranker, slots = self._reranker, self._rerank_slots
        if reranker is None or not documents:
            return None
        # 不等待：名額滿了就退回精排前的次序，慢的精排端點不能占滿伺服器的執行緒。
        if slots is not None and not slots.acquire(blocking=False):
            log.warning("rerank skipped: rerank slots are full")
            return None
        try:
            return reranker.score(query, documents, timeout=timeout)
        except RerankError as e:
            log.warning("rerank skipped: %s", e)
            return None
        finally:
            if slots is not None:
                slots.release()

    def _query_embedding(self, query: str) -> list[float] | None:
        if self._embedder is None:
            return None
        slots = self._embed_slots
        # 不等待：名額滿了就改走字面臂，慢的嵌入端點不能占滿伺服器的執行緒。
        if slots is not None and not slots.acquire(blocking=False):
            log.warning("semantic arm skipped: embed slots are full")
            return None
        try:
            return self._embedder.embed([query])[0]
        except EmbeddingError as e:
            log.warning("semantic arm skipped: %s", e)
            return None
        finally:
            if slots is not None:
                slots.release()


def _guided(pair: tuple[SearchHit, int]) -> bool:
    """函釋帶出這一條（有函釋提到查詢詞並解釋它）。"""
    return pair[1] > 0


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
