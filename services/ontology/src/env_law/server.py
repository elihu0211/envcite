"""Server bootstrap.

Config via env: ONTOLOGY_INTERNAL_TOKEN (required), ONTOLOGY_DSN (required),
ONTOLOGY_GRPC_PORT.
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

#: Seconds in-flight RPCs get to finish after SIGTERM. Below Kubernetes' default
#: terminationGracePeriodSeconds (30), so the kubelet's SIGKILL never lands first.
SHUTDOWN_GRACE_SECONDS = 25


def build_server(
    store: OntologyStore, token: str, address: str = "[::]:0"
) -> tuple[grpc.Server, int]:
    """Creates (not started) a server; returns it with the bound port."""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[InternalTokenInterceptor(token)],
    )
    # The lexicon is read once: terms imported later need a restart to reach
    # queries, as they need a re-index to reach `content_tsv`.
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
    """Drains the server on SIGTERM: refuses new RPCs and lets in-flight ones finish.

    Required, not optional: as PID 1 in the container, Python ignores SIGTERM
    unless a handler is installed, so the kubelet would wait out the grace period
    and SIGKILL mid-call.
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
