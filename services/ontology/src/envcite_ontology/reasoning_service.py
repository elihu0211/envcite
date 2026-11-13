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

from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite.ontology.v2 import reasoning_pb2_grpc
from envcite_grpc import remaining_seconds
from envcite_ontology import annotation_read, errors, identifiers
from envcite_ontology import reasoning_codec as codec
from envcite_ontology.identifiers import law_key
from envcite_ontology.reasoning import Result, evaluate_across
from envcite_ontology.reasoning.facts import Measurements, Permits, PerOccasionTimeline
from envcite_ontology.reasoning.scoring import ScoringInput
from envcite_ontology.store import LANGUAGE, OntologyStore
from envcite_ontology.telemetry import ReasoningMetrics

log = logging.getLogger(__name__)

#: 錯誤原因對指標的 outcome 標籤；不在表內的都記為 error。
_OUTCOMES = {
    errors.INVALID_REQUEST: "invalid",
    errors.BUSY: "busy",
    errors.EVALUATION_TIMEOUT: "deadline",
}

#: 同時進行的 `Evaluate` 數。
EVALUATE_SLOTS = 2
#: 呼叫沒有期限時，等名額的上限（秒）。
SLOT_WAIT_SECONDS = 5.0


class ReasoningServicer(reasoning_pb2_grpc.ReasoningServiceServicer):
    """`ReasoningService` 的 servicer：列出法規某日的標註陳述，與對案件事實推論（`Evaluate`）。"""

    def __init__(
        self,
        store: OntologyStore,
        evaluate_slots: int = EVALUATE_SLOTS,
        meter: Meter | None = None,
    ) -> None:
        self._store = store
        self._metrics = ReasoningMetrics(meter)
        self._slots = threading.BoundedSemaphore(evaluate_slots)

    @errors.maps_errors
    def ListStatements(
        self, request: pb.ListStatementsRequest, context: grpc.ServicerContext
    ) -> pb.ListStatementsResponse:
        _log_call(context, "ListStatements", identifiers.subject(request.identifier))
        v = errors.Validator()
        key = law_key(self._store, request.identifier, v)
        on = v.date("as_of", request.as_of)
        v.check()
        resource_id = self._store.resource_id(
            key, as_of=request.as_of, timeout=remaining_seconds(context)
        )
        if resource_id is None:
            raise identifiers.not_found(self._store, key, request.as_of, context)
        with self._store.read_connection(timeout=remaining_seconds(context)) as conn:
            return annotation_read.list_statements(conn, resource_id, on)

    @errors.maps_errors
    def Evaluate(
        self, request: pb.EvaluateRequest, context: grpc.ServicerContext
    ) -> pb.EvaluateResponse:
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

    def _evaluate_call(
        self, request: pb.EvaluateRequest, context: grpc.ServicerContext
    ) -> pb.EvaluateResponse:
        # 案件事實是客戶的案件內容：日誌只記數量。
        _log_call(context, "Evaluate", f"facts={len(request.facts)}")
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
        case = codec.case_facts(request, v, conduct_ended, result_on, cleared)
        permits = codec.permit_limits(request, v)
        actor_bases = codec.actor_bases(request, v)
        fact_units = codec.fact_units(request)
        act_groups = codec.act_groups(request)
        timeline = codec.timeline(request, v)
        scoring_inputs = codec.scoring_inputs(request, v)
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
                codec.measurements(request),
                permits,
                actor_bases,
                fact_units,
                act_groups,
                timeline,
                scoring_inputs,
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
        fact_units: dict[str, str],
        act_groups: dict[str, list[int]],
        timeline: PerOccasionTimeline | None,
        scoring_inputs: list[ScoringInput],
    ) -> pb.EvaluateResponse:
        try:
            with self._store.read_connection(timeout=remaining_seconds(context)) as conn:
                moved = evaluate_across(
                    conn,
                    conduct_on,
                    decided_on,
                    case,
                    LANGUAGE,
                    measurements=measurements,
                    permits=permits,
                    actor_bases=actor_bases,
                    fact_units=fact_units or None,
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
                    ids = codec.statement_ids(result)
                    where = annotation_read.sources(conn, ids, on)
                    used = annotation_read.definition_sources(
                        conn, {d for _, d in result.definitions_used}, on
                    )
                    # 行政規則以處分日判斷是否已發布、尚未停止（釋字第 287 號），各組陳述皆同
                    guidance = annotation_read.guidance_for(conn, ids, decided_on)
                    audit_res = (
                        codec.disposition_audit(request, result.scoring_results)
                        if include_audit
                        else None
                    )
                    return codec.outcome(result, where, uncovered, used, guidance, audit_res)

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


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
