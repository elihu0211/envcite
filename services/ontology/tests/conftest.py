import os
from collections.abc import Iterator

import grpc
import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from env_law.ontology.v1 import ontology_pb2_grpc
from env_law_ontology.migrate import apply_all
from env_law_ontology.server import build_server
from env_law_ontology.store import OntologyStore

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

    with PostgresContainer("postgres:18") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(url) as conn:
            apply_all(conn)
        yield url


#: Laws created by migration 0003. They belong to the schema's sample, not to any
#: test, so the cleanup below leaves them alone.
FIXTURE_LAWS = ("O0020001", "O0040001")


@pytest.fixture
def conn(dsn: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """A connection whose writes are undone, including committed ones.

    Rolling back is not enough: a test that commits leaves rows behind, and the next
    test sees them. Every corpus table hangs off `corpus.legal_resource` by a
    cascading foreign key, so deleting a law deletes all of it; the acquisition
    record has no sample rows and is emptied.
    """
    with psycopg.connect(dsn) as c:
        yield c
        c.rollback()
        c.execute(
            "DELETE FROM corpus.legal_resource WHERE local_id <> ALL(%s)",
            (list(FIXTURE_LAWS),),
        )
        c.execute("TRUNCATE source.import_problem, source.item, source.snapshot")
        c.commit()


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
