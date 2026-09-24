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

from env_law.consult.ports import LlmPort, OntologyPort
from env_law.consult.service import ConsultationServicer
from env_law.consult.v1 import consult_pb2_grpc

SERVICE_NAME = "env_law.consult.v1.ConsultationService"


def build_server(
    ontology: OntologyPort, llm: LlmPort, token: str, address: str = "[::]:0"
) -> tuple[grpc.Server, int]:
    """建立（但不啟動）伺服器；連同綁定的埠號一併回傳。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[InternalTokenInterceptor(token)],
    )
    consult_pb2_grpc.add_ConsultationServiceServicer_to_server(
        ConsultationServicer(ontology, llm), server
    )

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in ("", SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    port = server.add_insecure_port(address)
    return server, port


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.environ.get("CONSULT_INTERNAL_TOKEN", "")
    if not token:
        raise SystemExit("CONSULT_INTERNAL_TOKEN is required")

    # 本體 adapter 已存在（`adapters.GrpcOntology`）；語言模型的 adapter 則等待
    # 供應商的決定。在此失敗是刻意的——一個啟動後卻拒絕每一次呼叫的伺服器，看起來
    # 已經部署，實際上卻什麼都答不了。
    raise SystemExit("no LlmPort adapter yet: choose a language-model provider first")
