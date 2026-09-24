"""Create (if missing) or drop a worktree's own local development database.

Called by the aliases and pre-remove hook in `.config/wt.toml`, inside the ontology
environment because it already carries psycopg:

    uv run --directory services/ontology python ../../scripts/worktree-db.py ensure <name>
    uv run --directory services/ontology python ../../scripts/worktree-db.py drop <name>...

The admin connection follows the local PostgreSQL convention of
`appsettings.Development.json`; override it with `WORKTREE_PG_ADMIN_DSN`.
"""

import os
import sys

import psycopg
from psycopg import sql

ADMIN_DSN = os.environ.get(
    "WORKTREE_PG_ADMIN_DSN", "postgresql://postgres:postgres@localhost:5432/postgres"
)


def main() -> None:
    action, names = sys.argv[1], sys.argv[2:]
    try:
        conn = psycopg.connect(ADMIN_DSN, autocommit=True)
    except psycopg.OperationalError as e:
        # A stopped Postgres must not block removing a worktree; creating one must fail.
        print(f"worktree-db: cannot reach PostgreSQL: {e}", file=sys.stderr)
        sys.exit(0 if action == "drop" else 1)
    with conn:
        for name in names:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if action == "ensure" and not exists:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            elif action == "drop" and exists:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
