"""依檔名順序套用 SQL migration。

刻意不用 migration 框架：這個 schema 只有寥寥幾個檔案，而框架提供的是版本表、
自動產生與回滾語意——這裡只需要版本表，所以它就只是一張表。已套用的檔案絕不可
修改；請改為新增一個檔案。這條規則靠 checksum 比對強制執行，而不是靠人記得。
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
            # 資料庫由另一套基線建立：在其上套用現行檔案，會讓舊表與新表並存。
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
            # 逐檔提交：新加的列舉值要先提交才能使用，既有資料庫在升級時，後面的檔案才
            # 用得到前面的檔案加的值。全部放同一個交易，升級這條路徑在全新資料庫上測
            # 不到（型別是同一個交易建立時，新值可立即使用）。
            conn.commit()
    return applied


def main() -> None:
    """CLI 進入點：對 `ONTOLOGY_DSN` 套用 migration，然後結束。

    刻意與伺服器分開。啟動時就執行 migration 的伺服器，會讓每個 replica 都成為
    寫入者，把一次 rollout 變成競態；以獨立的任務執行，則讓 schema 變更有明確的
    開始與結束，可供其他元件等待。
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
