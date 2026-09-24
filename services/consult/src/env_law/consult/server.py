"""Server bootstrap.

Config via env: CONSULT_INTERNAL_TOKEN (required), ONTOLOGY_TARGET (required),
ONTOLOGY_INTERNAL_TOKEN (required), CONSULT_GRPC_PORT.
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
    """Creates (not started) a server; returns it with the bound port."""
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

    # The ontology adapter exists (`adapters.GrpcOntology`); the language-model one
    # waits on a provider decision. Failing here is deliberate — a server that
    # starts and then refuses every call would look deployed while being unable to
    # answer anything.
    raise SystemExit("no LlmPort adapter yet: choose a language-model provider first")
