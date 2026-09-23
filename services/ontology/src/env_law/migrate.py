"""Apply SQL migrations in filename order.

No migration framework on purpose: this schema is a handful of files, and a
framework buys a version table, autogeneration and rollback semantics — only the
version table is needed here, so it is a table. An applied file must never be
edited; add a new one instead. That rule is enforced by a checksum comparison
rather than by remembering it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from psycopg import Connection

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename   text PRIMARY KEY,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationChanged(RuntimeError):
    """An applied migration file was edited; add a new file instead."""


def apply_all(conn: Connection[tuple[object, ...]], directory: Path | None = None) -> list[str]:
    """Applies pending migrations; returns the filenames applied by this call."""
    directory = directory or MIGRATIONS_DIR
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_TABLE)
        cur.execute("SELECT filename, sha256 FROM schema_migration")
        seen: dict[str, str] = {str(f): str(h) for f, h in cur.fetchall()}

        for path in sorted(directory.glob("*.sql")):
            body = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if path.name in seen:
                if seen[path.name] != digest:
                    raise MigrationChanged(
                        f"{path.name} was already applied but its content changed; "
                        "add a new migration file instead of editing this one"
                    )
                continue
            cur.execute(body)
            cur.execute(
                "INSERT INTO schema_migration (filename, sha256) VALUES (%s, %s)",
                (path.name, digest),
            )
            applied.append(path.name)
    conn.commit()
    return applied


def main() -> None:
    """CLI entrypoint: apply migrations against `ONTOLOGY_DSN`, then exit.

    Separate from the server on purpose. A server that migrates on startup makes
    every replica a writer and turns a rollout into a race; running it as its own
    task means the schema change has a beginning and an end that something else
    can wait on.
    """
    import logging
    import os

    import psycopg

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")

    with psycopg.connect(dsn) as conn:
        applied = apply_all(conn)

    log = logging.getLogger(__name__)
    if applied:
        log.info("applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        log.info("schema already current")
