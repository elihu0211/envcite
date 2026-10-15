"""OpenTelemetry：與 platform 同一個約定——只認標準的 `OTEL_*` 設定鍵，**沒設
`OTEL_EXPORTER_OTLP_ENDPOINT` 就什麼都不做**（不載入 SDK、不連任何地方、不加攔截器）。

設了之後：追蹤（gRPC 伺服器攔截器，從呼叫端帶來的 traceparent 接續 platform 的追蹤）與指標都以
OTLP 匯出；資源屬性（服務名、pod、namespace）走標準的 `OTEL_SERVICE_NAME`、
`OTEL_RESOURCE_ATTRIBUTES`。自訂指標用 `opentelemetry.metrics.get_meter(...)`，沒設端點時
是無操作的實作，呼叫的成本可以忽略。

gRPC 伺服器的 RED 指標（rate、errors、duration）由 `envcite_grpc.metrics` 提供，
隨 `configure` 啟用。

租戶與案件內容不放進指標標籤：標籤的基數不受控；租戶只在追蹤上（platform 已記）。
"""

from __future__ import annotations

import os

import grpc

from envcite_grpc.metrics import RedMetricsInterceptor


def configure(service_name: str) -> list[grpc.ServerInterceptor]:
    """設定全域的追蹤與指標提供者，回傳要加到 `grpc.server` 的攔截器（沒設端點為空）。

    要在建立伺服器之前呼叫，且只呼叫一次。
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return []

    # 延後載入：沒開遙測的程序（含工作者與測試）不付 SDK 的載入成本。
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.grpc import server_interceptor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # `Resource.create` 會讀 OTEL_SERVICE_NAME 與 OTEL_RESOURCE_ATTRIBUTES；沒給服務名才用預設。
    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
        )
    )
    # RED 指標在最外層，量到的耗時含其他攔截器；追蹤的攔截器在其內。
    return [RedMetricsInterceptor(), server_interceptor()]


def client_interceptors() -> list[grpc.UnaryUnaryClientInterceptor]:
    """客戶端 channel 的追蹤攔截器（以 `grpc.intercept_channel` 套用）：把目前的追蹤接續到
    下游呼叫。沒設端點為空；須在 `configure` 之後呼叫。
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return []

    from opentelemetry.instrumentation.grpc import client_interceptor

    return [client_interceptor()]
