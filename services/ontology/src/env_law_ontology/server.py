"""gRPC 伺服器啟動；環境變數：ONTOLOGY_INTERNAL_TOKEN、ONTOLOGY_DSN（必填）、ONTOLOGY_GRPC_PORT。"""

from __future__ import annotations

import logging
import os
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from env_law.ontology.v1 import ontology_pb2_grpc
from env_law_grpc import InternalTokenInterceptor, stop_on_sigterm
from env_law_ontology.service import OntologyServicer
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter

SERVICE_NAME = "env_law.ontology.v1.OntologyService"


def build_server(
    store: OntologyStore, token: str, address: str = "[::]:0"
) -> tuple[grpc.Server, int]:
    """建立但不啟動；回傳伺服器與實際綁定的埠號。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[InternalTokenInterceptor(token)],
    )
    # lexicon 啟動時只讀一次：新匯入的用語須重啟才進入查詢。
    segmenter = Segmenter(store.lexicon())
    ontology_pb2_grpc.add_OntologyServiceServicer_to_server(
        OntologyServicer(store, segmenter), server
    )

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in ("", SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    port = server.add_insecure_port(address)
    return server, port


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = os.environ.get("ONTOLOGY_INTERNAL_TOKEN", "")
    if not token:
        raise SystemExit("ONTOLOGY_INTERNAL_TOKEN is required")

    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")
    store = OntologyStore.from_dsn(dsn)
    port = os.environ.get("ONTOLOGY_GRPC_PORT", "50051")

    server, bound = build_server(store, token, f"[::]:{port}")
    stop_on_sigterm(server)
    server.start()
    logging.getLogger(__name__).info("ontology gRPC server listening on :%d", bound)
    server.wait_for_termination()
