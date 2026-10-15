"""Telemetry: inert by default; the RED metrics (rate, errors, duration) once enabled."""

from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader

from envcite.consult.v2 import consult_pb2, consult_pb2_grpc
from envcite_consult.server import build_server
from envcite_grpc import configure_telemetry
from envcite_grpc.metrics import DURATION_METRIC, RedMetricsInterceptor
from tests.conftest import FakeOntology, call_metadata


def test_without_an_otlp_endpoint_telemetry_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert configure_telemetry("envcite-consult") == []


def _points(reader: InMemoryMetricReader) -> list[tuple[dict[str, Any], int]]:
    data = reader.get_metrics_data()
    out: list[tuple[dict[str, Any], int]] = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == DURATION_METRIC:
                    for point in metric.data.data_points:
                        assert isinstance(point, HistogramDataPoint)
                        out.append((dict(point.attributes or {}), point.count))
    return out


@pytest.fixture
def measured(
    ontology: FakeOntology,
) -> Iterator[tuple[consult_pb2_grpc.ConsultationServiceStub, InMemoryMetricReader]]:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    server, port = build_server(
        ontology, "127.0.0.1:0", interceptors=[RedMetricsInterceptor(meter)]
    )
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield consult_pb2_grpc.ConsultationServiceStub(channel), reader
    server.stop(grace=None)


def test_each_call_is_counted_by_method_and_status_without_tenant_or_content(
    measured: tuple[consult_pb2_grpc.ConsultationServiceStub, InMemoryMetricReader],
) -> None:
    stub, reader = measured
    question = "露天燃燒如何處罰"

    stub.Ground(consult_pb2.GroundRequest(question=question), metadata=call_metadata())
    stub.Ground(consult_pb2.GroundRequest(question=question), metadata=call_metadata())
    with pytest.raises(grpc.RpcError):  # 沒帶租戶
        stub.Ground(consult_pb2.GroundRequest(question=question), metadata=())

    base = {
        "rpc.system.name": "grpc",
        "rpc.service": "envcite.consult.v2.ConsultationService",
        "rpc.method": "Ground",
    }
    points = _points(reader)
    assert sorted(points, key=lambda p: p[0]["rpc.response.status_code"]) == [
        ({**base, "rpc.response.status_code": "INVALID_ARGUMENT"}, 1),
        ({**base, "rpc.response.status_code": "OK"}, 2),
    ]
    # 標籤全是有限集合：租戶與問句不在其中。
    assert all(question not in str(value) for attrs, _ in points for value in attrs.values())
