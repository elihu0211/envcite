"""Telemetry: inert by default, the Evaluate metrics, and the annotation health views."""

from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite.ontology.v2 import reasoning_pb2_grpc
from envcite_grpc import configure_telemetry
from envcite_ontology.reasoning_service import ReasoningServicer
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_ontology.telemetry import register_annotation_health
from tests.test_import import _scalar
from tests.verification_support import Conn, _water_draft


def test_without_an_otlp_endpoint_telemetry_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert configure_telemetry("envcite-ontology") == []


def _points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict[str, Any], Any]]:
    data = reader.get_metrics_data()
    out: list[tuple[dict[str, Any], Any]] = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    for point in metric.data.data_points:
                        value = getattr(point, "count", None) or getattr(point, "value", 0)
                        out.append((dict(point.attributes or {}), value))
    return out


@pytest.fixture
def served(
    store: OntologyStore,
) -> Iterator[tuple[reasoning_pb2_grpc.ReasoningServiceStub, InMemoryMetricReader]]:
    from concurrent import futures

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    reasoning_pb2_grpc.add_ReasoningServiceServicer_to_server(
        ReasoningServicer(store.connections, store.laws, meter=meter), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield reasoning_pb2_grpc.ReasoningServiceStub(channel), reader
    server.stop(0)


@pytest.mark.usefixtures("sample")
def test_evaluate_records_its_duration_by_outcome_and_leaves_no_one_in_flight(
    served: tuple[reasoning_pb2_grpc.ReasoningServiceStub, InMemoryMetricReader],
) -> None:
    stub, reader = served

    stub.Evaluate(pb.EvaluateRequest(conduct_on="2020-01-01", decided_on="2020-06-01"))
    with pytest.raises(grpc.RpcError):
        stub.Evaluate(pb.EvaluateRequest(conduct_on="yesterday", decided_on="2020-06-01"))

    durations = _points(reader, "envcite.reasoning.evaluate.duration")
    assert sorted(a["outcome"] for a, _ in durations) == ["invalid", "ok"]
    assert all(count == 1 for _, count in durations)
    in_flight = _points(reader, "envcite.reasoning.evaluate.in_flight")
    assert [value for _, value in in_flight] == [0]  # 進來一個、出去一個


def test_an_evaluation_that_cannot_get_a_slot_in_time_is_counted_as_busy(
    store: OntologyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 沒有期限時等名額的上限縮短，否則要等 5 秒；用期限會與客戶端的期限賽跑。
    monkeypatch.setattr("envcite_ontology.reasoning_service.SLOT_WAIT_SECONDS", 0.1)
    from concurrent import futures

    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    reasoning_pb2_grpc.add_ReasoningServiceServicer_to_server(
        ReasoningServicer(store.connections, store.laws, evaluate_slots=0, meter=meter),
        server,  # 沒有任何名額
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = reasoning_pb2_grpc.ReasoningServiceStub(channel)
            request = pb.EvaluateRequest(conduct_on="2020-01-01", decided_on="2020-06-01")
            with pytest.raises(grpc.RpcError) as error:
                stub.Evaluate(request)
    finally:
        server.stop(0)

    assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    outcomes = [a["outcome"] for a, _ in _points(reader, "envcite.reasoning.evaluate.duration")]
    assert outcomes == ["busy"]


def test_the_annotation_health_views_count_states_and_latest_gate_results(
    conn: Conn, sample: None, store: OntologyStore
) -> None:
    from envcite_ontology.verification.pipeline import verify

    _water_draft(conn, sample)
    verify(conn)

    rows = conn.execute("SELECT state, statements FROM annotation.statement_health").fetchall()
    states = {str(state): count for state, count in rows}
    assert states == {"extracted": 3}  # 罰則等義務確認才寫成陳述
    per_method = conn.execute(
        "SELECT method, sum(statements)::int FROM annotation.gate_health GROUP BY method"
    ).fetchall()
    assert {m: n for m, n in per_method} == {m: 3 for m, _ in per_method}  # 每個陳述每個方法一列
    assert _scalar(conn, "SELECT count(*) FROM annotation.gate_health WHERE method = 'policy'") == 0


def test_the_health_gauges_report_what_the_views_hold(
    conn: Conn, sample: None, store: OntologyStore
) -> None:
    _water_draft(conn, sample)
    reader = InMemoryMetricReader()
    register_annotation_health(
        store.connections, MeterProvider(metric_readers=[reader]).get_meter("test")
    )

    points = _points(reader, "envcite.annotation.statements")

    assert ({"state": "extracted"}, 3) in points
