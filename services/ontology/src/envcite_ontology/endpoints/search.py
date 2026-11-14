"""檢索的端點：混合檢索、函釋入口、可選的精排，與查詢向量的名額。"""

import threading
import time

import grpc
from opentelemetry.metrics import Meter

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite_grpc import remaining_seconds
from envcite_ontology import errors
from envcite_ontology.coverage import term_coverage
from envcite_ontology.embedding import Embedder, EmbeddingError
from envcite_ontology.endpoints.common import SERVICE_LOG, log_call
from envcite_ontology.guidance_search import CANDIDATES as GUIDANCE_CANDIDATES
from envcite_ontology.guidance_search import EXTRA as GUIDANCE_EXTRA
from envcite_ontology.guidance_search import fuse, guidance_phrases
from envcite_ontology.identifiers import jurisdiction_filter
from envcite_ontology.law_names import LawNames
from envcite_ontology.reranking import RerankError, Reranks, select
from envcite_ontology.search_tuning import SearchTuning
from envcite_ontology.store.ports import LawReader, Retrieval
from envcite_ontology.store.records import SearchHit
from envcite_ontology.telemetry import RerankMetrics
from envcite_segmenter import Segmenter

log = SERVICE_LOG

#: `limit` 為 0 時的預設命中數，與可請求的上限。
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


class SearchApi:
    """檢索的端點；嵌入與精排都是可選的加強，失敗或名額滿了就退回，查詢仍成功。"""

    def __init__(
        self,
        laws: LawReader,
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

    def search_articles(
        self, request: pb.SearchArticlesRequest, context: grpc.ServicerContext
    ) -> pb.SearchArticlesResponse:
        """在 `as_of` 當日有效的條文上做混合檢索，相關度由高到低。"""
        log_call(SERVICE_LOG, context, "SearchArticles", f"query_chars={len(request.query)}")
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
