"""Long-lived connection hygiene (envcite_grpc.connection): max age, keepalive, retries."""

import json
import time
from collections.abc import Iterator
from concurrent import futures

import grpc
import pytest
from grpc_health.v1 import health_pb2

from envcite_grpc.connection import (
    CLIENT_KEEPALIVE_MS,
    SERVER_MIN_PING_INTERVAL_MS,
    SERVER_OPTIONS,
    client_options,
)
from envcite_ontology.server import build_server
from envcite_ontology.store.ontology_store import OntologyStore


def test_the_servers_ping_floor_is_below_the_clients_ping_interval() -> None:
    """客戶端 ping 得比伺服器容許的更密，伺服器會以 too_many_pings 關連線。"""
    assert SERVER_MIN_PING_INTERVAL_MS < CLIENT_KEEPALIVE_MS
    assert ("grpc.keepalive_permit_without_calls", 1) in SERVER_OPTIONS
    assert dict(SERVER_OPTIONS)["grpc.max_connection_age_grace_ms"] > 0


def test_a_client_survives_the_server_ending_a_connection_that_reached_its_max_age(
    store: OntologyStore,
) -> None:
    """GOAWAY 之後客戶端重連，後續呼叫照常成功，不是失敗一次。"""
    short = (("grpc.max_connection_age_ms", 400), ("grpc.max_connection_age_grace_ms", 200))
    server, port = build_server(store, "127.0.0.1:0", options=short)
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}", options=client_options()) as channel:
            check = channel.unary_unary(
                "/grpc.health.v1.Health/Check",
                request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
                response_deserializer=health_pb2.HealthCheckResponse.FromString,
            )
            request = health_pb2.HealthCheckRequest()
            assert check(request, timeout=5).status == health_pb2.HealthCheckResponse.SERVING
            time.sleep(1.5)  # 超過最長壽命與寬限，連線已被伺服器結束
            assert check(request, timeout=5).status == health_pb2.HealthCheckResponse.SERVING
    finally:
        server.stop(0)


class _Flaky(grpc.GenericRpcHandler):
    """前 `fail` 次回指定狀態碼，之後成功；記下被呼叫幾次。"""

    def __init__(self, code: grpc.StatusCode, fail: int) -> None:
        self.code, self.fail, self.calls = code, fail, 0

    def service(
        self, handler_call_details: grpc.HandlerCallDetails
    ) -> grpc.RpcMethodHandler[bytes, bytes]:
        def behavior(request: bytes, context: grpc.ServicerContext) -> bytes:
            self.calls += 1
            if self.calls <= self.fail:
                context.abort(self.code, "scripted failure")
            return b"ok"

        return grpc.unary_unary_rpc_method_handler(behavior)


@pytest.fixture
def flaky_server() -> Iterator[tuple[str, grpc.GenericRpcHandler]]:
    handler = _Flaky(grpc.StatusCode.UNAVAILABLE, 0)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2), handlers=[handler])
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}", handler
    server.stop(0)


def _call(target: str, options: list[tuple[str, int | str]]) -> bytes:
    with grpc.insecure_channel(target, options=options) as channel:
        method: grpc.UnaryUnaryMultiCallable[bytes, bytes] = channel.unary_unary(
            "/envcite.ontology.v2.OntologyService/GetLaw"
        )
        reply: bytes = method(b"", timeout=5)
        return reply


def test_unavailable_is_retried_for_read_only_services(
    flaky_server: tuple[str, _Flaky],
) -> None:
    target, handler = flaky_server
    handler.code, handler.fail = grpc.StatusCode.UNAVAILABLE, 2

    reply = _call(target, client_options(["envcite.ontology.v2.OntologyService"]))

    assert reply == b"ok"
    assert handler.calls == 3


@pytest.mark.parametrize(
    "code", [grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.INVALID_ARGUMENT]
)
def test_capacity_and_bad_requests_are_not_retried(
    flaky_server: tuple[str, _Flaky], code: grpc.StatusCode
) -> None:
    """容量用盡是要立即失敗的訊號，重試只會加重負載；錯的請求重試也不會變對。"""
    target, handler = flaky_server
    handler.code, handler.fail = code, 5

    with pytest.raises(grpc.RpcError) as error:
        _call(target, client_options(["envcite.ontology.v2.OntologyService"]))

    assert error.value.code() == code
    assert handler.calls == 1


def test_a_service_not_listed_as_read_only_is_not_retried(
    flaky_server: tuple[str, _Flaky],
) -> None:
    target, handler = flaky_server
    handler.code, handler.fail = grpc.StatusCode.UNAVAILABLE, 2

    with pytest.raises(grpc.RpcError):
        _call(target, client_options(["envcite.some.other.Service"]))

    assert handler.calls == 1


def test_retries_are_throttled_so_a_failing_downstream_is_not_amplified() -> None:
    options = dict(client_options(["envcite.ontology.v2.OntologyService"]))

    config = json.loads(str(options["grpc.service_config"]))

    assert config["retryThrottling"] == {"maxTokens": 10, "tokenRatio": 0.1}
