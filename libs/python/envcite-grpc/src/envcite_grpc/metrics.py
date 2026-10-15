"""gRPC 伺服器的 RED 指標（rate、errors、duration）：每個一元呼叫一筆耗時直方圖。

只有一個儀器 `rpc.server.call.duration`（OpenTelemetry 語意約定的名稱與單位：秒），
三個訊號都從它導出：**rate** 是計數、**errors** 是 `rpc.response.status_code` 不是 `OK` 的計數、
**duration** 是分布。標籤只有 `rpc.system.name`、`rpc.service`、`rpc.method`、
`rpc.response.status_code`，都是有限集合；租戶與案件內容不進指標（基數不受控；租戶只在追蹤上）。

本模組只定義攔截器；是否啟用由 `envcite_grpc.telemetry.configure` 決定（沒設匯出端點就不加）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import grpc
from opentelemetry import metrics
from opentelemetry.metrics import Meter

METER_NAME = "envcite.grpc"
DURATION_METRIC = "rpc.server.call.duration"

#: 伺服器呼叫從毫秒到數十秒；預設的直方圖邊界以毫秒為單位，套在秒上會全擠在第一格。
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


class RedMetricsInterceptor(grpc.ServerInterceptor):
    """量測一元呼叫的耗時與結果狀態。沒有一元處理器的路徑（找不到的方法）原樣放行，不記。"""

    def __init__(self, meter: Meter | None = None) -> None:
        meter = meter or metrics.get_meter(METER_NAME)
        self._duration = meter.create_histogram(
            DURATION_METRIC,
            unit="s",
            description="gRPC 伺服器一元呼叫從收到到回應的耗時；rpc.response.status_code 為結果。",
            explicit_bucket_boundaries_advisory=list(DURATION_BUCKETS),
        )

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        service, _, method = handler_call_details.method.lstrip("/").partition("/")
        inner = handler.unary_unary

        def timed(request: Any, context: grpc.ServicerContext) -> Any:
            started = time.perf_counter()
            failed = True
            try:
                response = inner(request, context)
                failed = False
                return response
            finally:
                self._duration.record(
                    time.perf_counter() - started,
                    {
                        "rpc.system.name": "grpc",
                        "rpc.service": service,
                        "rpc.method": method,
                        "rpc.response.status_code": _status(context, failed).name,
                    },
                )

        return grpc.unary_unary_rpc_method_handler(
            timed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def _status(context: grpc.ServicerContext, failed: bool) -> grpc.StatusCode:
    """呼叫結束時的狀態：處理器 `abort` 或 `set_code` 過就用它；拋出未處理的例外是 UNKNOWN；
    客戶端已取消（沒設狀態、呼叫已不再有效）是 CANCELLED。"""
    # `code()` 是 gRPC 的實驗性 API，型別存根沒有它。
    code: grpc.StatusCode | None = context.code()  # type: ignore[attr-defined]
    if code is not None:
        return code
    if failed:
        return grpc.StatusCode.UNKNOWN
    return grpc.StatusCode.OK if context.is_active() else grpc.StatusCode.CANCELLED
