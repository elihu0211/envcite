"""gRPC servicer：把一次管線執行轉成事件串流。

事件順序是契約的一部分，不是實作細節。`grounding` 在第一個 `answer_chunk` 之前
送出，讓呼叫端能在任何文字出現之前先呈現來源——也讓文字中出現的引用，能與呼叫端
手上已有的集合比對。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import grpc

from env_law.consult import pipeline
from env_law.consult.ports import LlmPort, OntologyPort
from env_law.consult.v1 import consult_pb2, consult_pb2_grpc

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
    def __init__(self, ontology: OntologyPort, llm: LlmPort) -> None:
        self._ontology = ontology
        self._llm = llm

    # 方法名稱來自 proto；gRPC 依其原樣分派。
    def Consult(
        self, request: consult_pb2.ConsultRequest, context: grpc.ServicerContext
    ) -> Iterator[consult_pb2.ConsultResponse]:
        metadata = dict(context.invocation_metadata())
        tenant = str(metadata.get("x-tenant-id", ""))
        if not tenant:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "x-tenant-id is required")
        log.info("Consult tenant=%s request_id=%s", tenant, metadata.get("x-request-id", "-"))

        yield _stage("retrieve")
        outcome = pipeline.run(
            request.question, as_of=request.as_of, ontology=self._ontology, llm=self._llm
        )

        yield _stage("ground")
        yield consult_pb2.ConsultResponse(
            grounding=consult_pb2.Grounding(
                citations=[
                    consult_pb2.Citation(code=c.code, iri=c.iri, label=c.label)
                    for c in outcome.grounding
                ]
            )
        )

        if outcome.refusal is not None:
            yield consult_pb2.ConsultResponse(
                refused=consult_pb2.Refused(
                    reason=_REASON[outcome.refusal.reason], detail=outcome.refusal.detail
                )
            )
            return

        yield _stage("generate")
        # 沒有拒答即表示有 chunks；管線恰好只會設定其中之一。
        chunks = outcome.chunks or iter(())
        answer: list[str] = []
        for chunk in chunks:
            answer.append(chunk)
            yield consult_pb2.ConsultResponse(answer_chunk=consult_pb2.AnswerChunk(text=chunk))

        yield _stage("verify")
        yield consult_pb2.ConsultResponse(
            completed=consult_pb2.Completed(
                cited_codes=pipeline.cited_codes("".join(answer), outcome.grounding),
                confidence=_CONFIDENCE[pipeline.confidence_of(outcome.grounding, outcome.articles)],
            )
        )


def _stage(name: str) -> consult_pb2.ConsultResponse:
    return consult_pb2.ConsultResponse(stage_started=consult_pb2.StageStarted(stage=name))
