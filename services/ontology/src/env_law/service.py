"""gRPC servicer and internal-auth interceptor."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import Any

import grpc

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law.store import OntologyStore

log = logging.getLogger(__name__)

_UNAUTHENTICATED_PREFIXES = ("/grpc.health.v1.Health/",)


class OntologyServicer(ontology_pb2_grpc.OntologyServiceServicer):
    def __init__(self, store: OntologyStore) -> None:
        self._store = store

    def GetLaw(
        self, request: ontology_pb2.GetLawRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.GetLawResponse:
        _log_call(context, "GetLaw", request.id)
        law = self._store.get_law(request.id)
        if law is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"law '{request.id}' not found")
        return ontology_pb2.GetLawResponse(
            law=ontology_pb2.Law(
                id=law.id,
                iri=law.iri,
                title=law.title,
                language=law.language,
                date_document=law.date_document,
            )
        )

    def ListArticles(
        self, request: ontology_pb2.ListArticlesRequest, context: grpc.ServicerContext
    ) -> ontology_pb2.ListArticlesResponse:
        _log_call(context, "ListArticles", request.law_id)
        articles = self._store.list_articles(request.law_id)
        if articles is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"law '{request.law_id}' not found")
        return ontology_pb2.ListArticlesResponse(
            articles=[ontology_pb2.Article(iri=a.iri, number=a.number) for a in articles]
        )


def _log_call(context: grpc.ServicerContext, method: str, subject: str) -> None:
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )


class InternalTokenInterceptor(grpc.ServerInterceptor):
    """Rejects calls without `authorization: Bearer <token>` (health checks exempt)."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("internal token must not be empty")
        self._expected = f"Bearer {token}".encode()

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        if handler_call_details.method.startswith(_UNAUTHENTICATED_PREFIXES):
            return continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata or ())
        provided = str(metadata.get("authorization", "")).encode()
        if hmac.compare_digest(provided, self._expected):
            return continuation(handler_call_details)

        return grpc.unary_unary_rpc_method_handler(_deny)


def _deny(_request: Any, context: grpc.ServicerContext) -> None:
    context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid or missing internal token")
