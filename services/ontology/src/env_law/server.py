"""伺服器啟動。

以環境變數設定：ONTOLOGY_INTERNAL_TOKEN（必填）、ONTOLOGY_DSN（必填）、
ONTOLOGY_GRPC_PORT。
"""

from __future__ import annotations

import logging
import os
import signal
from concurrent import futures

import grpc
from env_law_grpc import InternalTokenInterceptor
from env_law_segmenter import Segmenter
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from env_law.ontology.v1 import ontology_pb2_grpc
from env_law.service import OntologyServicer
from env_law.store import OntologyStore

SERVICE_NAME = "env_law.ontology.v1.OntologyService"

#: 收到 SIGTERM 後，進行中的 RPC 可用來完成的秒數。低於 Kubernetes 預設的
#: terminationGracePeriodSeconds（30），因此 kubelet 的 SIGKILL 絕不會先到。
SHUTDOWN_GRACE_SECONDS = 25


def build_server(
    store: OntologyStore, token: str, address: str = "[::]:0"
) -> tuple[grpc.Server, int]:
    """建立（但不啟動）伺服器；連同綁定的埠號一併回傳。"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[InternalTokenInterceptor(token)],
    )
    # lexicon 只讀取一次：之後匯入的用語要重新啟動才會進入查詢，正如它們要重新
    # 建立索引才會進入 `content_tsv`。
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


def stop_on_sigterm(server: grpc.Server) -> None:
    """收到 SIGTERM 時排空伺服器：拒絕新的 RPC，並讓進行中的 RPC 完成。

    這是必要而非選配：作為容器中的 PID 1，Python 除非安裝了 handler，否則會忽略
    SIGTERM，於是 kubelet 會等完寬限期，然後在呼叫途中 SIGKILL。
    """
    signal.signal(signal.SIGTERM, lambda *_: server.stop(SHUTDOWN_GRACE_SECONDS))


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
