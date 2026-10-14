"""伺服器啟動。

以環境變數設定：ONTOLOGY_TARGET（必填）、CONSULT_GRPC_PORT。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from envcite.consult.v2 import consult_pb2_grpc
from envcite_consult.adapters import GrpcOntology
from envcite_consult.ports import OntologyPort
from envcite_consult.service import ConsultationServicer
from envcite_grpc import (
    SERVER_OPTIONS,
    client_interceptors,
    client_options,
    configure_telemetry,
    stop_on_sigterm,
)

SERVICE_NAME = "envcite.consult.v2.ConsultationService"

WORKERS = 10
#: 同時進行的 RPC 上限；超過就以 RESOURCE_EXHAUSTED 立即失敗，而不是無限排隊。
MAX_CONCURRENT_RPCS = 2 * WORKERS


def build_server(
    ontology: OntologyPort,
    address: str = "[::]:0",
    options: Sequence[tuple[str, int]] = SERVER_OPTIONS,
    interceptors: Sequence[grpc.ServerInterceptor] = (),
    health_servicer: health.HealthServicer | None = None,
) -> tuple[grpc.Server, int]:
    """建立（但不啟動）伺服器；連同綁定的埠號一併回傳。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=WORKERS),
        interceptors=list(interceptors),
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        options=options,
    )
    consult_pb2_grpc.add_ConsultationServiceServicer_to_server(
        ConsultationServicer(ontology), server
    )

    health_servicer = health_servicer or health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in ("", SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    port = server.add_insecure_port(address)
    return server, port


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    target = _required("ONTOLOGY_TARGET")
    port = os.environ.get("CONSULT_GRPC_PORT", "50052")

    # round_robin 與 platform 相同：`dns:///` 目標解析出 ontology 的每一個 pod，
    # 呼叫逐一分散，而非全部黏在第一條連線上。ontology 的兩個服務都是唯讀、確定性的，
    # 可以對 UNAVAILABLE 重試；keepalive 與連線壽命見 envcite_grpc.connection。
    # 沒設 OTEL_EXPORTER_OTLP_ENDPOINT 時什麼都不做（見 envcite_grpc.telemetry）；設了，
    # platform 的追蹤經伺服器攔截器接進來，再經客戶端攔截器接到 ontology。
    interceptors = configure_telemetry("envcite-consult")
    channel = grpc.intercept_channel(
        grpc.insecure_channel(
            target,
            options=client_options(
                ["envcite.ontology.v1.OntologyService", "envcite.ontology.v1.ReasoningService"]
            ),
        ),
        *client_interceptors(),
    )
    health_servicer = health.HealthServicer()
    server, bound = build_server(
        GrpcOntology(channel),
        f"[::]:{port}",
        interceptors=interceptors,
        health_servicer=health_servicer,
    )
    stop_on_sigterm(server, health_servicer)
    server.start()
    logging.getLogger(__name__).info("consult gRPC server listening on :%d", bound)
    server.wait_for_termination()
