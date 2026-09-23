import os
from collections.abc import Iterator

import grpc
import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from env_law.migrate import apply_all
from env_law.ontology.v1 import ontology_pb2_grpc
from env_law.server import build_server
from env_law.store import OntologyStore

TOKEN = "test-internal-token"


@pytest.fixture(scope="session")
def dsn() -> Iterator[str]:
    """A real PostgreSQL, migrated.

    Not a double: half of this service's read logic lives in SQL — integer-array
    reading order, the derived column, the CHECK constraints — and testing against a
    double is not testing that half.

    `ONTOLOGY_TEST_DSN` points at an already-running server (CI attaches one as a
    job service; locally it can be anything). Without it, a container is started,
    which needs a Docker daemon.
    """
    external = os.environ.get("ONTOLOGY_TEST_DSN")
    if external:
        with psycopg.connect(external) as conn:
            apply_all(conn)
        yield external
        return

    with PostgresContainer("postgres:18-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(url) as conn:
            apply_all(conn)
        yield url


@pytest.fixture(scope="session")
def store(dsn: str) -> Iterator[OntologyStore]:
    with OntologyStore.from_dsn(dsn) as s:
        yield s


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
