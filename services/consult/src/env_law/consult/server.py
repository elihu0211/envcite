"""伺服器啟動。

以環境變數設定：CONSULT_INTERNAL_TOKEN（必填）、ONTOLOGY_TARGET（必填）、
ONTOLOGY_INTERNAL_TOKEN（必填）、CONSULT_GRPC_PORT。
"""

from __future__ import annotations

import logging
import os
from concurrent import futures

import grpc
from env_law_grpc import InternalTokenInterceptor
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from env_law.consult.adapters import GrpcOntology
from env_law.consult.ports import OntologyPort
from env_law.consult.service import ConsultationServicer
from env_law.consult.v2 import consult_pb2_grpc

SERVICE_NAME = "env_law.consult.v2.ConsultationService"


def build_server(
    ontology: OntologyPort, token: str, address: str = "[::]:0"
) -> tuple[grpc.Server, int]:
    """建立（但不啟動）伺服器；連同綁定的埠號一併回傳。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[InternalTokenInterceptor(token)],
    )
    consult_pb2_grpc.add_ConsultationServiceServicer_to_server(
        ConsultationServicer(ontology), server
    )

    health_servicer = health.HealthServicer()
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
    token = _required("CONSULT_INTERNAL_TOKEN")
    target = _required("ONTOLOGY_TARGET")
    ontology_token = _required("ONTOLOGY_INTERNAL_TOKEN")
    port = os.environ.get("CONSULT_GRPC_PORT", "50052")

    # round_robin 與 platform 相同：`dns:///` 目標解析出 ontology 的每一個 pod，
    # 呼叫逐一分散，而非全部黏在第一條連線上。
    channel = grpc.insecure_channel(target, options=[("grpc.lb_policy_name", "round_robin")])
    server, bound = build_server(GrpcOntology(channel, ontology_token), token, f"[::]:{port}")
    server.start()
    logging.getLogger(__name__).info("consult gRPC server listening on :%d", bound)
    server.wait_for_termination()
