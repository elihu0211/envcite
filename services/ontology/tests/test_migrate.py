import psycopg
import pytest

from env_law_ontology.migrate import MigrationChanged, apply_all


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
