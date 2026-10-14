"""gRPC 伺服器啟動；環境變數：ONTOLOGY_DSN（必填）、ONTOLOGY_GRPC_PORT。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from envcite.ontology.v1 import ontology_pb2_grpc, reasoning_pb2_grpc
from envcite_grpc import SERVER_OPTIONS, configure_telemetry, stop_on_sigterm
from envcite_ontology.reasoning_service import ReasoningServicer
from envcite_ontology.service import OntologyServicer
from envcite_ontology.settings import ServerSettings, load
from envcite_ontology.store import OntologyStore
from envcite_ontology.telemetry import register_annotation_health
from envcite_segmenter import Segmenter

SERVICE_NAME = "envcite.ontology.v1.OntologyService"
REASONING_SERVICE_NAME = "envcite.ontology.v1.ReasoningService"

WORKERS = 10
#: 同時進行的 RPC 上限。固定大小的執行緒池之外，超過的請求排隊；上限讓排隊有界，超過就
#: 以 RESOURCE_EXHAUSTED 立即失敗，而不是無限等下去。
MAX_CONCURRENT_RPCS = 2 * WORKERS


def build_server(
    store: OntologyStore,
    address: str = "[::]:0",
    options: Sequence[tuple[str, int]] = SERVER_OPTIONS,
    interceptors: Sequence[grpc.ServerInterceptor] = (),
    health_servicer: health.HealthServicer | None = None,
) -> tuple[grpc.Server, int]:
    """建立但不啟動；回傳伺服器與實際綁定的埠號。`health_servicer` 讓呼叫端在關閉時切換狀態。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=WORKERS),
        interceptors=list(interceptors),
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=options,
    )
    # lexicon 啟動時只讀一次：新匯入的用語須重啟才進入查詢。
    segmenter = Segmenter(store.lexicon())
    ontology_pb2_grpc.add_OntologyServiceServicer_to_server(
        OntologyServicer(store, segmenter), server
    )

    reasoning_pb2_grpc.add_ReasoningServiceServicer_to_server(ReasoningServicer(store), server)
    reflection.enable_server_reflection(
        (SERVICE_NAME, REASONING_SERVICE_NAME, health.SERVICE_NAME, reflection.SERVICE_NAME), server
    )

    health_servicer = health_servicer or health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in ("", SERVICE_NAME, REASONING_SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    port = server.add_insecure_port(address)
    return server, port


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load(ServerSettings)
    store = OntologyStore.from_dsn(settings.ontology_dsn)

    # 沒設 OTEL_EXPORTER_OTLP_ENDPOINT 時什麼都不做（見 envcite_grpc.telemetry）。
    interceptors = configure_telemetry("envcite-ontology")
    register_annotation_health(store)
    health_servicer = health.HealthServicer()
    server, bound = build_server(
        store,
        f"[::]:{settings.ontology_grpc_port}",
        interceptors=interceptors,
        health_servicer=health_servicer,
    )
    stop_on_sigterm(server, health_servicer)
    server.start()
    logging.getLogger(__name__).info("ontology gRPC server listening on :%d", bound)
    server.wait_for_termination()
