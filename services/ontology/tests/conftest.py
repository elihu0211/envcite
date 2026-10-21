import os
from collections.abc import Iterator
from pathlib import Path

import grpc
import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from envcite.ontology.v1 import ontology_pb2_grpc, reasoning_pb2_grpc
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.migrate import apply_all
from envcite_ontology.server import build_server
from envcite_ontology.store import OntologyStore
from tests.sample import sample_laws, stored_snapshot


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

    with PostgresContainer("pgvector/pgvector:pg18") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(url) as conn:
            apply_all(conn)
        yield url


@pytest.fixture
def conn(dsn: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """A connection whose writes are undone, including committed ones.

    Rolling back is not enough: a test that commits leaves rows behind, and the next
    test sees them. Every corpus table hangs off `corpus.legal_resource` by a
    cascading foreign key, so deleting a law deletes all of it; the acquisition
    record is emptied.
    """
    with psycopg.connect(dsn) as c:
        yield c
        c.rollback()
        c.execute("RESET ROLE")
        c.execute("DELETE FROM corpus.legal_resource")
        c.execute("TRUNCATE source.import_problem, source.item, source.snapshot")
        # 索引是內容定址的衍生物：不隨法規連鎖刪除，測試之間要自己清。
        c.execute("DELETE FROM retrieval.passage")
        # 標註隨單元連鎖刪除；推導活動與詞彙不隨之而去，測試之間要自己清（概念先於方案）。
        c.execute("DELETE FROM guidance.document")  # 公文不隨法規連鎖刪除，且指向推導活動
        c.execute("DELETE FROM annotation.derivation")
        c.execute("DELETE FROM vocab.concept")
        c.execute("DELETE FROM vocab.concept_scheme")
        c.commit()


@pytest.fixture
def sample(conn: psycopg.Connection[tuple[object, ...]], tmp_path: Path) -> None:
    """Two real-shaped laws (`tests/sample.py`), imported the way production imports them."""
    stored_snapshot(conn, tmp_path, sample_laws())
    importer.run(conn, tmp_path, Moj())


@pytest.fixture(scope="session")
def store(dsn: str) -> Iterator[OntologyStore]:
    with OntologyStore.from_dsn(dsn) as s:
        yield s


@pytest.fixture(scope="session")
def server_address(store: OntologyStore) -> Iterator[str]:
    server, port = build_server(store, "127.0.0.1:0")
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


@pytest.fixture
def reasoning_stub(channel: grpc.Channel) -> reasoning_pb2_grpc.ReasoningServiceStub:
    return reasoning_pb2_grpc.ReasoningServiceStub(channel)


def call_metadata() -> tuple[tuple[str, str], ...]:
    return (
        ("x-tenant-id", "acme"),
        ("x-request-id", "req-1"),
    )
