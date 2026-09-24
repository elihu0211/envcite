"""服務對服務 gRPC 的內部 token 認證。

在諮詢服務成為第二個消費端時才從本體服務抽出——不是更早。只為一個呼叫端設計的
介面，其實是為想像中的第二個呼叫端而設計，而想像出來的那個通常是錯的。

它守的是服務之間的邊界，不是租戶之間的：它只回答「這是不是來自系統內部的呼叫」，
別無其他。租戶授權屬於擁有租戶狀態的那個服務。
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

import grpc

#: 健康檢查必須在呼叫端持有 token 之前就能回應——探測 readiness 的 orchestrator
#: 並未經過認證，也不應該需要。
UNAUTHENTICATED_PREFIXES = ("/grpc.health.v1.Health/",)


class InternalTokenInterceptor(grpc.ServerInterceptor):
    """拒絕沒有 `authorization: Bearer <token>` 的呼叫（健康檢查除外）。"""

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
        # 常數時間比較：會洩漏時間差的 token 檢查，就會洩漏 token。
        if hmac.compare_digest(provided, self._expected):
            return continuation(handler_call_details)

        return grpc.unary_unary_rpc_method_handler(_deny)


def _deny(_request: Any, context: grpc.ServicerContext) -> None:
    context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid or missing internal token")
