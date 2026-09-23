from collections.abc import Iterator

import grpc
import pytest

from env_law.ontology.v1 import ontology_pb2_grpc
from env_law.server import build_server
from env_law.store import OntologyStore

TOKEN = "test-internal-token"


@pytest.fixture(scope="session")
def store() -> OntologyStore:
    return OntologyStore.from_sample()


@pytest.fixture(scope="session")
def server_address(store: OntologyStore) -> Iterator[str]:
    server, port = build_server(store, TOKEN, "127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(grace=None)


@pytest.fixture
def channel(server_address: str) -> Iterator[grpc.Channel]:
    with grpc.insecure_channel(server_address) as ch:
        yield ch


@pytest.fixture
def stub(channel: grpc.Channel) -> ontology_pb2_grpc.OntologyServiceStub:
    return ontology_pb2_grpc.OntologyServiceStub(channel)


def auth() -> tuple[tuple[str, str], ...]:
    return (
        ("authorization", f"Bearer {TOKEN}"),
        ("x-tenant-id", "acme"),
        ("x-request-id", "req-1"),
    )
