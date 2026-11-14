"""gRPC servicer。

錯誤一律丟 `errors.ServiceError`，由 `errors.maps_errors` 轉成帶 ErrorInfo 的狀態。
"""

import logging
import threading
import time

import grpc
from opentelemetry.metrics import Meter

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
from envcite_ontology.search_tuning import SearchTuning
from envcite_ontology.store.ports import LawGraphReader, LawReader, Retrieval
from envcite_ontology.store.records import LawGraphRecord, LawKey, SearchHit
from envcite_ontology.telemetry import RerankMetrics
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)


#: `limit` 為 0 時的預設命中數，與可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
DEFAULT_GRAPH_DEPTH = 2
MAX_GRAPH_DEPTH = 4
DEFAULT_GRAPH_CHILD_LIMIT = 20
MAX_GRAPH_CHILD_LIMIT = 60

_EDGE_KINDS = {
    "delegation": pb.GRAPH_EDGE_KIND_DELEGATION,
    "sub_delegation": pb.GRAPH_EDGE_KIND_SUB_DELEGATION,
}

_VIOLATION_KINDS = {
    "duty": pb.VIOLATION_REFERENCE_KIND_DUTY,
    "delegated_rule": pb.VIOLATION_REFERENCE_KIND_DELEGATED_RULE,
}

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
        laws: LawReader,
        graph: LawGraphReader,
        retrieval: Retrieval,
        segmenter: Segmenter,
        law_names: LawNames,
        *,
        embedder: Embedder | None = None,
        reranker: Reranks | None = None,
        tuning: SearchTuning | None = None,
        meter: Meter | None = None,
    ) -> None:
        tuning = tuning or SearchTuning()
        self._laws = laws
        self._graph = graph
        self._retrieval = retrieval
        self._law_names = law_names
        self._tuning = tuning
        self._segmenter = segmenter
        self._embedder = embedder
        # 名額滿了就略過語意臂。None 表示不限（測試直接建構、不走伺服器預算時）。
        self._embed_slots = (
            threading.BoundedSemaphore(tuning.embed_slots)
            if tuning.embed_slots is not None
            else None
        )
        self._reranker = reranker
        self._rerank_slots = (
            threading.BoundedSemaphore(tuning.rerank_slots)
            if tuning.rerank_slots is not None
            else None
        )
        self._rerank_metrics = RerankMetrics(meter)

    def _request_key(self, identifier: pb.LawIdentifier, as_of: str) -> LawKey:
        """必填的自然鍵與 `as_of` 一次驗證，違規一次回報。"""
        v = errors.Validator()
        key = law_key(self._laws, identifier, v)
        v.date("as_of", as_of)
        v.check()
        return key

    @errors.maps_errors
    def GetLaw(self, request: pb.GetLawRequest, context: grpc.ServicerContext) -> pb.GetLawResponse:
        """依自然鍵取得一部法規；不存在回 `LAW_NOT_FOUND`。"""
        _log_call(context, "GetLaw", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        law = self._laws.get_law(key, as_of=request.as_of, timeout=remaining_seconds(context))
        if law is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
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
        """依閱讀順序列出一部法規的條文。"""
        _log_call(context, "ListArticles", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        timeout = remaining_seconds(context)
        articles = self._laws.list_articles(key, as_of=request.as_of, timeout=timeout)
        if articles is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        headings = self._laws.list_headings(key, as_of=request.as_of, timeout=timeout) or []
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
        """列出 `as_of` 當日有有效版本的法規摘要與授權母法關聯。"""
        _log_call(context, "ListLaws", request.jurisdiction or "all")
        v = errors.Validator()
        v.date("as_of", request.as_of)
        jurisdiction = jurisdiction_filter(self._laws, request.jurisdiction, v)
        category_prefix = _category_prefix_filter(request.category_prefix, v)
        v.check()

        laws = self._laws.list_laws(
            as_of=request.as_of,
            jurisdiction=jurisdiction,
            category_prefix=category_prefix,
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
                    document_type=item.document_type,
                    child_count=item.child_count,
                    parent_count=item.parent_count,
                    guidance_count=item.guidance_count,
                )
            )
        return pb.ListLawsResponse(laws=items)

    @errors.maps_errors
    def GetArticleByIri(
        self, request: pb.GetArticleByIriRequest, context: grpc.ServicerContext
    ) -> pb.GetArticleByIriResponse:
        """依條文版本的標準 IRI 取得單一條文全文與中繼資料。"""
        iri = request.iri.strip()
        _log_call(context, "GetArticleByIri", iri or "<empty>")
        v = errors.Validator()
        if not iri:
            v.add("iri", errors.EMPTY, "iri is required")
        v.check()
        record = self._laws.get_article_by_iri(iri, timeout=remaining_seconds(context))
        if record is None:
            raise errors.not_found("NOT_FOUND", f"article not found: {iri}")
        return pb.GetArticleByIriResponse(
            article=pb.ArticleDetail(
                iri=record.iri,
                identifier=pb.LawIdentifier(
                    jurisdiction=record.jurisdiction,
                    id_scheme=record.id_scheme,
                    local_id=record.local_id,
                ),
                law_title=record.law_title,
                number=record.number,
                content=record.content,
                language=record.language,
                date_document=record.date_document,
                norm_kind=record.norm_kind,
                document_type=record.document_type,
                repealed_on=record.repealed_on or "",
            )
        )

    @errors.maps_errors
    def GetLawGraph(
        self, request: pb.GetLawGraphRequest, context: grpc.ServicerContext
    ) -> pb.GetLawGraphResponse:
        """取得以特定法規為焦點（或依分類總覽）的多層授權知識圖譜。"""
        has_id = request.HasField("identifier")
        subject = (
            identifiers.subject(request.identifier)
            if has_id
            else f"overview:{request.category_prefix or 'all'}"
        )
        _log_call(context, "GetLawGraph", subject)

        v = errors.Validator()
        key = law_key(self._laws, request.identifier, v) if has_id else None
        v.date("as_of", request.as_of)
        category_prefix = _category_prefix_filter(request.category_prefix, v)
        upstream_depth = _bounded_int(
            request.upstream_depth,
            default=DEFAULT_GRAPH_DEPTH,
            max_value=MAX_GRAPH_DEPTH,
            field="upstream_depth",
            v=v,
        )
        downstream_depth = _bounded_int(
            request.downstream_depth,
            default=DEFAULT_GRAPH_DEPTH,
            max_value=MAX_GRAPH_DEPTH,
            field="downstream_depth",
            v=v,
        )
        child_limit = _bounded_int(
            request.child_limit,
            default=DEFAULT_GRAPH_CHILD_LIMIT,
            max_value=MAX_GRAPH_CHILD_LIMIT,
            field="child_limit",
            v=v,
        )
        v.check()

        graph = self._graph.get_law_graph(
            key,
            as_of=request.as_of,
            category_prefix=category_prefix,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
            child_limit=child_limit,
            child_document_type=request.child_document_type,
            timeout=remaining_seconds(context),
        )
        if graph is None:
            assert key is not None
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        return _to_graph_response(graph)

    @errors.maps_errors
    def ListInvalidations(
        self, request: pb.ListInvalidationsRequest, context: grpc.ServicerContext
    ) -> pb.ListInvalidationsResponse:
        """列出 `as_of` 當日因違憲而失效的條文單元。"""
        _log_call(context, "ListInvalidations", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        found = self._laws.list_invalidations(
            key,
            as_of=request.as_of,
            article_number=request.article_number,
            timeout=remaining_seconds(context),
        )
        if found is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
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
        """在 `as_of` 當日有效的條文上做混合檢索，相關度由高到低。"""
        _log_call(context, "SearchArticles", f"query_chars={len(request.query)}")
        v = errors.Validator()
        terms = self._segmenter.to_tsvector_input(request.query)
        if not terms:
            v.add("query", errors.EMPTY, "query must not be empty")
        limit = request.limit or DEFAULT_SEARCH_LIMIT
        if not 0 < limit <= MAX_SEARCH_LIMIT:
            v.add("limit", errors.OUT_OF_RANGE, f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        v.date("as_of", request.as_of)
        jurisdiction = jurisdiction_filter(self._laws, request.jurisdiction, v)
        v.check()

        # 精排要看得比 limit 深：取較深的候選，精排後再取前 limit 條。
        # 沒有精排模型時，請求的 rerank 當作沒有。
        rerank = request.rerank and self._reranker is not None
        depth = max(limit, self._tuning.rerank_candidates) if rerank else limit
        hits = self._retrieval.search(
            terms,
            tokenizer=self._segmenter.tokenizer_id,
            as_of=request.as_of,
            limit=depth,
            embedding=self._query_embedding(request.query),
            model=self._embedder.model if self._embedder else "",
            max_df=self._tuning.lexical_max_df,
            laws=self._law_names.find(request.query),
            jurisdiction=jurisdiction,
            timeout=remaining_seconds(context),
        )
        searched = self._retrieval.searched_terms(
            terms,
            tokenizer=self._segmenter.tokenizer_id,
            max_df=self._tuning.lexical_max_df,
            timeout=remaining_seconds(context),
        )
        ranked = [(h, 0) for h in hits]
        if request.include_guidance and (phrases := guidance_phrases(request.query)):
            ranked = fuse(
                hits,
                self._retrieval.guidance_hits(
                    phrases,
                    as_of=request.as_of,
                    limit=GUIDANCE_CANDIDATES,
                    jurisdiction=jurisdiction,
                    max_df=self._tuning.guidance_max_df,
                    timeout=remaining_seconds(context),
                ),
                depth,
            )
        scored: list[tuple[tuple[SearchHit, int], float | None]] = [(r, None) for r in ranked]
        if request.rerank and not rerank and ranked:
            self._rerank_metrics.outcomes.add(1, {"outcome": "unconfigured"})
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
        reranker, slots, recorded = self._reranker, self._rerank_slots, self._rerank_metrics
        if reranker is None or not documents:
            return None
        # 不等待：名額滿了就退回精排前的次序，慢的精排端點不能占滿伺服器的執行緒。
        if slots is not None and not slots.acquire(blocking=False):
            log.warning("rerank skipped: rerank slots are full")
            recorded.outcomes.add(1, {"outcome": "busy"})
            return None
        started = time.monotonic()
        try:
            scores = reranker.score(query, documents, timeout=timeout)
        except RerankError as e:
            log.warning("rerank skipped: %s", e)
            recorded.duration.record(time.monotonic() - started, {"outcome": "error"})
            recorded.outcomes.add(1, {"outcome": "error"})
            return None
        finally:
            if slots is not None:
                slots.release()
        recorded.duration.record(time.monotonic() - started, {"outcome": "ok"})
        recorded.outcomes.add(1, {"outcome": "reranked"})
        return scores

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


def _category_prefix_filter(prefix: str, v: errors.Validator) -> str | None:
    """可選的全國法規分類字首（單一大寫字母 A–Z）。"""
    if not prefix:
        return None
    if len(prefix) != 1 or not ("A" <= prefix <= "Z"):
        v.add(
            "category_prefix",
            errors.UNKNOWN_VALUE,
            "category_prefix must be a single uppercase letter A-Z",
        )
    return prefix


def _bounded_int(
    value: int, *, default: int, max_value: int, field: str, v: errors.Validator
) -> int:
    actual = default if value == 0 else value
    if not 0 < actual <= max_value:
        v.add(field, errors.OUT_OF_RANGE, f"{field} must be between 1 and {max_value}")
    return actual


def _to_graph_response(graph: LawGraphRecord) -> pb.GetLawGraphResponse:
    focus = (
        pb.LawIdentifier(
            jurisdiction=graph.focus_jurisdiction,
            id_scheme=graph.focus_id_scheme,
            local_id=graph.focus_local_id,
        )
        if graph.focus_jurisdiction and graph.focus_id_scheme and graph.focus_local_id
        else None
    )
    return pb.GetLawGraphResponse(
        focus_identifier=focus,
        nodes=[
            pb.LawGraphNode(
                identifier=pb.LawIdentifier(
                    jurisdiction=n.jurisdiction,
                    id_scheme=n.id_scheme,
                    local_id=n.local_id,
                ),
                iri=n.iri,
                title=n.title,
                norm_kind=n.norm_kind,
                document_type=n.document_type,
                date_document=n.date_document,
                hierarchy_tier=n.hierarchy_tier,
                total_children=n.total_children,
                truncated_children=n.truncated_children,
                article_count=n.article_count,
                invalidation_count=n.invalidation_count,
                guidance_count=n.guidance_count,
            )
            for n in graph.nodes
        ],
        edges=[
            pb.LawGraphEdge(
                source=pb.LawIdentifier(
                    jurisdiction=e.source_jurisdiction,
                    id_scheme=e.source_id_scheme,
                    local_id=e.source_local_id,
                ),
                target=pb.LawIdentifier(
                    jurisdiction=e.target_jurisdiction,
                    id_scheme=e.target_id_scheme,
                    local_id=e.target_local_id,
                ),
                kind=_EDGE_KINDS.get(e.kind, pb.GRAPH_EDGE_KIND_UNSPECIFIED),
                anchor_articles=e.anchor_articles,
                all_rooted=e.all_rooted,
            )
            for e in graph.edges
        ],
        violation_links=[
            pb.ArticleViolationLink(
                penalty_article=v.penalty_article,
                penalty_path=v.penalty_path,
                kind=_VIOLATION_KINDS.get(v.kind, pb.VIOLATION_REFERENCE_KIND_UNSPECIFIED),
                target_article=v.target_article,
                target_path=v.target_path,
            )
            for v in graph.violation_links
        ],
        guidance_links=[
            pb.GuidanceLink(
                article_number=g.article_number,
                path=g.path,
                document_no=g.document_no,
                issuer=g.issuer,
                issued_on=g.issued_on,
                subject=g.subject,
                source_url=g.source_url,
            )
            for g in graph.guidance_links
        ],
        child_groups=[
            pb.ChildGroupCount(document_type=cg.document_type, count=cg.count)
            for cg in graph.child_groups
        ],
    )
