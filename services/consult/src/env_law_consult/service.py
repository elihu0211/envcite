"""gRPC servicer：把一次管線執行轉成證據或拒答。

本服務不執行語言模型，因此沒有串流：證據一次備齊，由呼叫端交給它自己的模型。
"""

from __future__ import annotations

import logging

import grpc

from env_law.consult.v2 import consult_pb2, consult_pb2_grpc
from env_law_consult import pipeline
from env_law_consult.ports import OntologyPort

log = logging.getLogger(__name__)

_CONFIDENCE = {
    pipeline.Confidence.LOW: consult_pb2.CONFIDENCE_LOW,
    pipeline.Confidence.HIGH: consult_pb2.CONFIDENCE_HIGH,
}

_REASON = {
    pipeline.RefusalReason.NO_EVIDENCE: consult_pb2.Refused.REASON_NO_EVIDENCE,
    pipeline.RefusalReason.OUT_OF_SCOPE: consult_pb2.Refused.REASON_OUT_OF_SCOPE,
    pipeline.RefusalReason.MISSING_INPUT: consult_pb2.Refused.REASON_MISSING_INPUT,
}


class ConsultationServicer(consult_pb2_grpc.ConsultationServiceServicer):
    def __init__(self, ontology: OntologyPort) -> None:
        self._ontology = ontology

    # 方法名稱來自 proto；gRPC 依其原樣分派。
    def Ground(
        self, request: consult_pb2.GroundRequest, context: grpc.ServicerContext
    ) -> consult_pb2.GroundResponse:
        metadata = dict(context.invocation_metadata())
        tenant = str(metadata.get("x-tenant-id", ""))
        if not tenant:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "x-tenant-id is required")
        log.info("Ground tenant=%s request_id=%s", tenant, metadata.get("x-request-id", "-"))

        try:
            outcome = pipeline.run(request.question, as_of=request.as_of, ontology=self._ontology)
        except grpc.RpcError as err:
            # 本體的狀態碼原樣轉出：無效的 `as_of` 仍是 INVALID_ARGUMENT，本體不可用仍是
            # UNAVAILABLE（可重試）。否則兩者都會變成 UNKNOWN，呼叫端無從區分。
            if isinstance(err, grpc.Call):
                context.abort(err.code(), err.details() or "")
            raise

        if isinstance(outcome, pipeline.Refusal):
            return consult_pb2.GroundResponse(
                refused=consult_pb2.Refused(reason=_REASON[outcome.reason], detail=outcome.detail)
            )
        return consult_pb2.GroundResponse(
            evidence=consult_pb2.Evidence(
                citations=[
                    consult_pb2.Citation(code=c.code, iri=c.iri, label=c.label, text=c.text)
                    for c in outcome.citations
                ],
                confidence=_CONFIDENCE[outcome.confidence],
            )
        )
