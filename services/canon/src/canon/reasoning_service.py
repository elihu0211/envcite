"""`ReasoningService`：標註與推論的唯讀 gRPC 介面。

請求在邊界一次驗證完（欄位錯誤全部回報），之後內部只用型別明確的 `date` 與案件事實；推論程式
與標註的讀取都不重做解析。`Evaluate` 是 CPU 與資料庫都吃重的呼叫，所以有自己的名額：同步伺服器
的執行緒池是固定大小的，不限制的話幾個推論就能占滿，連查一條法規都排不到。
"""

import logging
import threading
import time
from datetime import date

import grpc
import psycopg.errors
from opentelemetry.metrics import Meter

from canon import annotation_read, errors, identifiers
from canon.case_facts import CaseFactExtractor, Unavailable
from canon.clock import Today
from canon.embedding import EmbeddingError
from canon.endpoints.common import log_call
from canon.identifiers import law_key
from canon.reasoning.engine import Result
from canon.reasoning.facts.case_inputs import Measurements, Permits, PerOccasionTimeline
from canon.reasoning.scoring.models import ScoringInput
from canon.reasoning.transition import evaluate_across
from canon.reasoning_codec import disposition as disposition_codec
from canon.reasoning_codec import outcome as outcome_codec
from canon.reasoning_codec import request as request_codec
from canon.reasoning_codec.disposition import DispositionInput
from canon.store.ports import LawReader, ReadConnections
from canon.store.records import LANGUAGE
from canon.telemetry import ReasoningMetrics
from canon.verification.models import ModelError
from envcite.canon.v1 import reasoning_pb2 as pb
from envcite.canon.v1 import reasoning_pb2_grpc
from envcite_grpc import remaining_seconds

log = logging.getLogger(__name__)

#: 錯誤原因對指標的 outcome 標籤；不在表內的都記為 error。
_OUTCOMES = {
    errors.INVALID_REQUEST: "invalid",
    errors.BUSY: "busy",
    errors.EVALUATION_TIMEOUT: "deadline",
    errors.EXTRACTOR_NOT_CONFIGURED: "unconfigured",
    errors.EXTRACTION_UNAVAILABLE: "unavailable",
}

#: 同時進行的 `Evaluate` 數。
EVALUATE_SLOTS = 2
#: 呼叫沒有期限時，等名額的上限（秒）。
SLOT_WAIT_SECONDS = 5.0
#: 同時進行的 `ExtractFacts` 數：每個占一次模型呼叫（數秒），上限讓排隊有界；要小於工作執行緒數。
EXTRACT_SLOTS = 3
#: 問句字數上限。
MAX_QUESTION_CHARS = 2000


class ReasoningServicer(reasoning_pb2_grpc.ReasoningServiceServicer):
    """`ReasoningService` 的 servicer：列出法規某日的標註陳述，與對案件事實推論（`Evaluate`）。"""

    def __init__(
        self,
        connections: ReadConnections,
        laws: LawReader,
        evaluate_slots: int = EVALUATE_SLOTS,
        meter: Meter | None = None,
        today: Today = date.today,
        extractor: CaseFactExtractor | None = None,
        extract_slots: int = EXTRACT_SLOTS,
    ) -> None:
        self._today = today
        self._connections = connections
        self._laws = laws
        self._metrics = ReasoningMetrics(meter)
        self._slots = threading.BoundedSemaphore(evaluate_slots)
        self._extractor = extractor
        self._extract_slots = threading.BoundedSemaphore(extract_slots)

    @errors.maps_errors
    def ListStatements(
        self, request: pb.ListStatementsRequest, context: grpc.ServicerContext
    ) -> pb.ListStatementsResponse:
        """列出資源在 `as_of` 當日有效、已確認的陳述。"""
        log_call(log, context, "ListStatements", identifiers.subject(request.identifier))
        v = errors.Validator()
        key = law_key(self._laws, request.identifier, v)
        as_of = v.date("as_of", request.as_of)
        v.check()
        resource_id = self._laws.resource_id(
            key, as_of=request.as_of, timeout=remaining_seconds(context)
        )
        if resource_id is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        with self._connections.read_connection(timeout=remaining_seconds(context)) as conn:
            return annotation_read.list_statements(conn, resource_id, as_of or self._today())

    @errors.maps_errors
    def Evaluate(
        self, request: pb.EvaluateRequest, context: grpc.ServicerContext
    ) -> pb.EvaluateResponse:
        """對案件事實推論；唯讀，有自己的並行名額與期限。"""
        started = time.monotonic()
        outcome = "error"
        try:
            response = self._evaluate_call(request, context)
            outcome = "ok"
            return response
        except errors.ServiceError as error:
            outcome = _OUTCOMES.get(error.reason, "error")
            raise
        finally:
            self._metrics.duration.record(time.monotonic() - started, {"outcome": outcome})

    @errors.maps_errors
    def ExtractFacts(
        self, request: pb.ExtractFactsRequest, context: grpc.ServicerContext
    ) -> pb.ExtractFactsResponse:
        """把問句裡明說的事實對到概念代碼；有自己的並行名額與期限，失敗讓呼叫端退回現行做法。"""
        started = time.monotonic()
        outcome = "error"
        try:
            response = self._extract_call(request, context)
            outcome = "ok"
            self._metrics.extracted_facts.record(len(response.facts))
            return response
        except errors.ServiceError as error:
            outcome = _OUTCOMES.get(error.reason, "error")
            raise
        finally:
            self._metrics.extract_duration.record(time.monotonic() - started, {"outcome": outcome})

    def _extract_call(
        self, request: pb.ExtractFactsRequest, context: grpc.ServicerContext
    ) -> pb.ExtractFactsResponse:
        # 問句是客戶的案件內容：日誌只記字數。
        log_call(log, context, "ExtractFacts", f"chars={len(request.question)}")
        if self._extractor is None:
            raise errors.ServiceError(
                grpc.StatusCode.FAILED_PRECONDITION,
                errors.EXTRACTOR_NOT_CONFIGURED,
                "no fact extraction model is configured",
            )
        v = errors.Validator()
        if not request.question.strip():
            v.add("question", errors.EMPTY, "question must not be empty")
        elif len(request.question) > MAX_QUESTION_CHARS:
            v.add(
                "question",
                errors.OUT_OF_RANGE,
                f"question must not exceed {MAX_QUESTION_CHARS} characters",
            )
        v.check()

        budget = remaining_seconds(context)
        if not self._extract_slots.acquire(timeout=SLOT_WAIT_SECONDS if budget is None else budget):
            raise errors.ServiceError(
                grpc.StatusCode.RESOURCE_EXHAUSTED, errors.BUSY, "extraction capacity is in use"
            )
        try:
            if not context.is_active():  # 等名額的時候呼叫端已經走了
                raise errors.ServiceError(
                    grpc.StatusCode.CANCELLED, errors.EVALUATION_TIMEOUT, "call is no longer active"
                )
            try:
                extraction = self._extractor.extract(
                    lambda: self._connections.read_connection(timeout=remaining_seconds(context)),
                    request.question,
                    self._today(),
                )
            except (ModelError, EmbeddingError, Unavailable) as error:
                log.warning("ExtractFacts unavailable: %s", type(error).__name__)
                raise errors.ServiceError(
                    grpc.StatusCode.UNAVAILABLE,
                    errors.EXTRACTION_UNAVAILABLE,
                    "the extraction or embedding endpoint did not answer",
                ) from error
        finally:
            self._extract_slots.release()
        return pb.ExtractFactsResponse(
            facts=[
                pb.ExtractedFact(
                    concept=f.concept,
                    name=extraction.names[f.concept],
                    value=_fact_value(f.value),
                    quote=f.quote,
                )
                for f in extraction.facts
            ],
            candidates=extraction.candidates,
        )

    def _evaluate_call(
        self, request: pb.EvaluateRequest, context: grpc.ServicerContext
    ) -> pb.EvaluateResponse:
        # 案件事實是客戶的案件內容：日誌只記數量。
        log_call(log, context, "Evaluate", f"facts={len(request.facts)}")
        v = errors.Validator()
        conduct_on = v.date("conduct_on", request.conduct_on, required=True)
        decided_on = v.date("decided_on", request.decided_on, required=True)
        conduct_ended = v.date("conduct_ended_on", request.conduct_ended_on)
        result_on = v.date("result_on", request.result_on)
        cleared = v.date(
            "administrative_penalty_facts.criminal_cleared_on",
            request.administrative_penalty_facts.criminal_cleared_on,
        )
        if conduct_on is not None and decided_on is not None and conduct_on > decided_on:
            v.add("conduct_on", errors.DATE_ORDER, "conduct_on must not be after decided_on")
        case = request_codec.case_facts(request, v, conduct_ended, result_on, cleared)
        permits = request_codec.permit_limits(request, v)
        actor_bases = request_codec.actor_bases(request, v)
        act_groups = request_codec.act_groups(request)
        timeline = request_codec.timeline(request, v)
        scoring_inputs = disposition_codec.scoring_inputs(request, v)
        disposition = disposition_codec.disposition_input(request, v)
        v.check()
        assert conduct_on is not None and decided_on is not None  # check() 已擋下

        budget = remaining_seconds(context)
        if not self._slots.acquire(timeout=SLOT_WAIT_SECONDS if budget is None else budget):
            raise errors.ServiceError(
                grpc.StatusCode.RESOURCE_EXHAUSTED, errors.BUSY, "evaluation capacity is in use"
            )
        self._metrics.in_flight.add(1)
        try:
            if not context.is_active():  # 等名額的時候呼叫端已經走了
                raise errors.ServiceError(
                    grpc.StatusCode.CANCELLED, errors.EVALUATION_TIMEOUT, "call is no longer active"
                )
            return self._evaluate(
                context,
                request,
                conduct_on,
                decided_on,
                case,
                request_codec.measurements(request),
                permits,
                actor_bases,
                act_groups,
                timeline,
                scoring_inputs,
                disposition,
            )
        finally:
            self._metrics.in_flight.add(-1)
            self._slots.release()

    def _evaluate(
        self,
        context: grpc.ServicerContext,
        request: pb.EvaluateRequest,
        conduct_on: date,
        decided_on: date,
        case: dict[str, float | list[float | str]],
        measurements: Measurements,
        permits: Permits,
        actor_bases: list[str],
        act_groups: dict[str, list[int]],
        timeline: PerOccasionTimeline | None,
        scoring_inputs: list[ScoringInput],
        disposition: DispositionInput | None,
    ) -> pb.EvaluateResponse:
        try:
            with self._connections.read_connection(timeout=remaining_seconds(context)) as conn:
                moved = evaluate_across(
                    conn,
                    conduct_on,
                    decided_on,
                    case,
                    LANGUAGE,
                    measurements=measurements,
                    permits=permits,
                    actor_bases=actor_bases,
                    act_groups=act_groups or None,
                    timeline=timeline,
                    scoring_inputs=scoring_inputs,
                )

                def outcome(
                    result: Result,
                    on: date,
                    uncovered: tuple[tuple[str, str, str], ...],
                    *,
                    include_audit: bool = False,
                ) -> pb.Outcome:
                    ids = outcome_codec.statement_ids(result)
                    where = annotation_read.sources(conn, ids, on)
                    used = annotation_read.definition_sources(
                        conn, {d for _, d in result.definitions_used}, on
                    )
                    # 行政規則以處分日判斷是否已發布、尚未停止（釋字第 287 號），各組陳述皆同。
                    # 行政法院裁判以裁處日不早於裁判日附上，同樣不進前提。
                    guidance = annotation_read.guidance_for(conn, ids, decided_on)
                    decisions = annotation_read.decisions_for(conn, ids, decided_on)
                    audit_res = (
                        disposition_codec.disposition_audit(disposition, result.scoring_results)
                        if include_audit
                        else None
                    )
                    gap_names = annotation_read.concept_names(
                        conn, ids, outcome_codec.gap_codes(result)
                    )
                    return outcome_codec.outcome(
                        result, where, uncovered, used, guidance, decisions, audit_res, gap_names
                    )

                return pb.EvaluateResponse(
                    at_decision=outcome(
                        moved.at_decision,
                        decided_on,
                        moved.at_decision_uncovered_laws,
                        include_audit=True,
                    ),
                    earlier=[
                        pb.EarlierLaw(
                            first_day=e.as_of.isoformat(),
                            outcome=outcome(e.result, e.as_of, e.uncovered_laws),
                        )
                        for e in moved.earlier
                    ],
                )
        except psycopg.errors.QueryCanceled as error:  # statement_timeout：期限用完
            raise errors.ServiceError(
                grpc.StatusCode.DEADLINE_EXCEEDED,
                errors.EVALUATION_TIMEOUT,
                "evaluation did not finish within the deadline",
            ) from error


def _fact_value(value: bool | float) -> pb.FactValue:
    """旗標或數字；數字以十進位文字表示（同契約的 `Decimal`），不用浮點的科學記號。"""
    if isinstance(value, bool):
        return pb.FactValue(flag=value)
    text = f"{value:f}".rstrip("0").rstrip(".")
    return pb.FactValue(number=pb.Decimal(value=text or "0"))
