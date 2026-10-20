"""`ReasoningService`：標註與推論的唯讀 gRPC 介面。

請求在邊界一次驗證完（欄位錯誤全部回報），之後內部只用型別明確的 `date` 與案件事實；推論程式
與標註的讀取都不重做解析。`Evaluate` 是 CPU 與資料庫都吃重的呼叫，所以有自己的名額：同步伺服器
的執行緒池是固定大小的，不限制的話幾個推論就能占滿，連查一條法規都排不到。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date

import grpc
import psycopg.errors
from opentelemetry.metrics import Meter

from envcite.ontology.v1 import reasoning_pb2 as pb
from envcite.ontology.v1 import reasoning_pb2_grpc
from envcite_grpc import remaining_seconds
from envcite_ontology import annotation_read, errors
from envcite_ontology import reasoning_codec as codec
from envcite_ontology.reasoning import Result, evaluate_across
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
        _log_call(context, "ListStatements", request.law_id)
        v = errors.Validator()
        on = v.date("as_of", request.as_of)
        v.check()
        resource_id = self._store.resource_id(request.law_id, as_of=request.as_of)
        if resource_id is None:
            raise self._not_found(request.law_id, request.as_of)
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
        if conduct_on is not None and decided_on is not None and conduct_on > decided_on:
            v.add("conduct_on", errors.DATE_ORDER, "conduct_on must not be after decided_on")
        case = codec.case_facts(request, v, conduct_ended, result_on)
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
            return self._evaluate(context, conduct_on, decided_on, case)
        finally:
            self._metrics.in_flight.add(-1)
            self._slots.release()

    def _evaluate(
        self,
        context: grpc.ServicerContext,
        conduct_on: date,
        decided_on: date,
        case: dict[str, float | list[float | str]],
    ) -> pb.EvaluateResponse:
        try:
            with self._store.read_connection(timeout=remaining_seconds(context)) as conn:
                moved = evaluate_across(conn, conduct_on, decided_on, case, LANGUAGE)

                def outcome(result: Result, on: date, uncovered: tuple[str, ...]) -> pb.Outcome:
                    where = annotation_read.sources(conn, codec.statement_ids(result), on)
                    used = annotation_read.definition_sources(
                        conn, {d for _, d in result.definitions_used}, on
                    )
                    return codec.outcome(result, where, uncovered, used)

                return pb.EvaluateResponse(
                    at_decision=outcome(
                        moved.at_decision, decided_on, moved.at_decision_uncovered_laws
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

    def _not_found(self, law_id: str, as_of: str) -> errors.ServiceError:
        # 法規不存在與該日無施行版本是兩件事，呼叫端要能分辨。
        if self._store.law_exists(law_id):
            when = f"in force on {as_of}" if as_of else "in force today"
            return errors.not_found(
                errors.AS_OF_NOT_COVERED, f"law '{law_id}' has no version {when}"
            )
        return errors.not_found(errors.LAW_NOT_FOUND, f"law '{law_id}' not found")


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )
