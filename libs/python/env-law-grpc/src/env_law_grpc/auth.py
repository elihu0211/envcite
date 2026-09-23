"""Internal-token authentication for service-to-service gRPC.

Extracted from the ontology service when the consultation service became its
second consumer — not before. An interface designed for one caller is designed
for an imagined second one, and the imagined one is usually wrong.

This guards the perimeter between services, not tenants: it answers "is this a
call from inside the system", and nothing else. Tenant authorisation belongs to
whichever service owns tenant state.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

import grpc

#: Health checks must answer before a caller can hold a token — an orchestrator
#: probing readiness is not authenticated and should not need to be.
UNAUTHENTICATED_PREFIXES = ("/grpc.health.v1.Health/",)


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
        if handler_call_details.method.startswith(UNAUTHENTICATED_PREFIXES):
            return continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata or ())
        provided = str(metadata.get("authorization", "")).encode()
        # Constant-time: a token check that leaks timing leaks the token.
        if hmac.compare_digest(provided, self._expected):
            return continuation(handler_call_details)

        return grpc.unary_unary_rpc_method_handler(_deny)


def _deny(_request: Any, context: grpc.ServicerContext) -> None:
    context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid or missing internal token")
