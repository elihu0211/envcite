"""依檔名順序套用 SQL migration。

不用框架：只需要版本表。已套用的檔案不可修改，由 checksum 比對強制。
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
    """已套用的 migration 檔案遭到修改；請改為新增一個檔案。"""


def apply_all(conn: Connection[tuple[object, ...]], directory: Path | None = None) -> list[str]:
    """套用尚未執行的 migration；回傳本次呼叫所套用的檔名。"""
    directory = directory or MIGRATIONS_DIR
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_TABLE)
        cur.execute("SELECT filename, sha256 FROM schema_migration")
        seen: dict[str, str] = {str(f): str(h) for f, h in cur.fetchall()}

        paths = sorted(directory.glob("*.sql"))
        orphans = sorted(seen.keys() - {p.name for p in paths})
        if orphans:
            # 資料庫來自另一套基線；硬套會讓新舊表並存。
            raise MigrationChanged(
                f"applied migrations no longer on disk: {', '.join(orphans)}; "
                "this database predates the current baseline — recreate it"
            )

        for path in paths:
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
            # 逐檔提交：新增的列舉值須先提交，後面的檔案才能用；單一交易在全新資料庫測不出此問題。
            conn.commit()
    return applied


def main() -> None:
    """對 `ONTOLOGY_DSN` 套用 migration 後結束。

    與伺服器分開：避免每個 replica 啟動時都寫入而使 rollout 競態，並讓 schema 變更有可等待的終點。
    """
    import logging

    import psycopg

    from env_law_ontology.settings import DatabaseSettings, load

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load(DatabaseSettings)

    with psycopg.connect(settings.ontology_dsn) as conn:
        applied = apply_all(conn)

    log = logging.getLogger(__name__)
    if applied:
        log.info("applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        log.info("schema already current")
