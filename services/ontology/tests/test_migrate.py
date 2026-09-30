from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from env_law_ontology.migrate import MIGRATIONS_DIR, MigrationChanged, apply_all


def test_a_database_from_another_baseline_is_refused(dsn: str) -> None:
    """Applying the current files over a retired baseline would leave both schemas."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO schema_migration (filename, sha256) VALUES ('0001_retired.sql', 'x')"
        )
        try:
            with pytest.raises(MigrationChanged, match=r"0001_retired\.sql"):
                apply_all(conn)
        finally:
            conn.rollback()


def test_a_database_at_the_first_baseline_upgrades_to_the_current_files(
    dsn: str, tmp_path: Path
) -> None:
    """An existing database gets only the new files, and a new enum value must be
    committed before a later file uses it — which a fresh database, where the type is
    created in the same transaction, never exercises."""
    name = "upgrade_path"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))[:4]:
            (baseline / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        with psycopg.connect(make_conninfo(dsn, dbname=name)) as conn:
            assert len(apply_all(conn, baseline)) == 4
            upgraded = apply_all(conn)
            assert upgraded[0].startswith("0005") and len(upgraded) >= 3
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO corpus.lifecycle_event (resource_id, kind, event_date)"
                    " SELECT id, 'unknown', NULL FROM corpus.legal_resource LIMIT 1"
                )
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
