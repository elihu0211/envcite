import os
from collections.abc import Iterator
from pathlib import Path

import grpc
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from testcontainers.community.postgres import PostgresContainer

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc, reasoning_pb2_grpc
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.ingest import importer
from envcite_ontology.migrate import apply_all
from envcite_ontology.server import build_server
from envcite_ontology.store.ontology_store import OntologyStore
from tests.sample import sample_laws, stored_snapshot


def _worker_database(server_dsn: str) -> str:
    """The DSN itself, or under xdist a fresh database named after the worker."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return server_dsn
    name = f"{conninfo_to_dict(server_dsn)['dbname']}_{worker}"
    with psycopg.connect(server_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_conninfo(server_dsn, dbname=name)


# 0027 checks then creates cluster-wide roles. Applied migration files are
# checksummed, so the race cannot be fixed there. xdist workers migrate one
# shared server at the same moment; the lock makes that creation one at a time.
_MIGRATE_LOCK = "envcite_ontology_test_migrate"


def _migrate(server_dsn: str, url: str) -> None:
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        with psycopg.connect(url) as conn:
            apply_all(conn)
        return
    with psycopg.connect(server_dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(hashtext(%s))", (_MIGRATE_LOCK,))
        try:
            with psycopg.connect(url) as conn:
                apply_all(conn)
        finally:
            admin.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_MIGRATE_LOCK,))


@pytest.fixture(scope="session")
def dsn() -> Iterator[str]:
    """A real PostgreSQL, migrated.

    Not a double: half of this service's read logic lives in SQL — integer-array
    reading order, the derived column, the CHECK constraints — and testing against a
    double is not testing that half.

    `ENVCITE_ONTOLOGY_TEST_DSN` points at an already-running server (CI attaches one as a
    job service; locally it can be anything). Without it, a container is started,
    which needs a Docker daemon.

    Under pytest-xdist each worker gets its own database on that server: tests clean
    up by deleting rows, so workers sharing one database would see each other's.
    """
    external = os.environ.get("ENVCITE_ONTOLOGY_TEST_DSN")
    if external:
        url = _worker_database(external)
        _migrate(external, url)
        yield url
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
        c.execute(
            "DELETE FROM guidance.document"
        )  # 公文不隨法規連鎖刪除；下架的停止適用參照來源項目
        for table in ("source.import_problem", "source.item", "source.snapshot"):
            c.execute(f"DELETE FROM {table}")  # 有外鍵參照（停止適用→項目），不能 TRUNCATE
        # 索引是內容定址的衍生物：不隨法規連鎖刪除，測試之間要自己清。
        c.execute("DELETE FROM retrieval.passage")
        c.execute("DELETE FROM retrieval.term_stat")
        c.execute("DELETE FROM retrieval.term_stat_total")
        # 標註隨單元連鎖刪除；推導活動與詞彙不隨之而去，測試之間要自己清（概念先於方案）。
        c.execute("DELETE FROM corpus.constitutional_ruling")  # 裁判不隨法規連鎖刪除
        c.execute("DELETE FROM corpus.ruling_scan")
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


def identifier(
    local_id: str, jurisdiction: str = "TW-CENTRAL", id_scheme: str = "moj_pcode"
) -> ontology_pb2.LawIdentifier:
    """法規的複合自然鍵；預設是中央法規（全國法規資料庫 pcode）。"""
    return ontology_pb2.LawIdentifier(
        jurisdiction=jurisdiction, id_scheme=id_scheme, local_id=local_id
    )


def call_metadata() -> tuple[tuple[str, str], ...]:
    return (
        ("x-tenant-id", "acme"),
        ("x-request-id", "req-1"),
    )
