"""`canon migrate` 的契約：依檔名套用、已套用的檔不可改、資料庫來自別的基線就拒絕、逐檔提交。

以暫存目錄裡的合成遷移檔驗證，不依賴 `migrations/` 的內容。
"""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from canon.db import Conn
from canon.migrate import MigrationChanged, apply_all


@pytest.fixture
def empty(dsn: str) -> Iterator[Conn]:
    """沒有任何遷移的空資料庫；名字跟著測試資料庫走，平行的 worker 才不會互相覆蓋。"""
    name = f"{conninfo_to_dict(dsn)['dbname']}_migrate"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(make_conninfo(dsn, dbname=name)) as conn:
            yield conn
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _files(directory: Path, **files: str) -> Path:
    directory.mkdir(exist_ok=True)
    for name, body in files.items():
        (directory / f"{name}.sql").write_text(body, encoding="utf-8")
    return directory


def test_only_the_files_not_yet_applied_are_applied(empty: Conn, tmp_path: Path) -> None:
    directory = _files(tmp_path / "m", **{"0001_a": "CREATE TABLE a (x int);"})
    assert apply_all(empty, directory) == ["0001_a.sql"]

    _files(directory, **{"0002_b": "CREATE TABLE b (x int);"})

    assert apply_all(empty, directory) == ["0002_b.sql"]
    assert apply_all(empty, directory) == []


def test_an_applied_file_that_changed_is_refused(empty: Conn, tmp_path: Path) -> None:
    directory = _files(tmp_path / "m", **{"0001_a": "CREATE TABLE a (x int);"})
    apply_all(empty, directory)

    _files(directory, **{"0001_a": "CREATE TABLE a (x bigint);"})

    with pytest.raises(MigrationChanged, match=r"0001_a\.sql"):
        apply_all(empty, directory)


def test_a_database_from_another_baseline_is_refused(dsn: str) -> None:
    """Applying the current files over a retired baseline would leave both schemas."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO schema_migration (filename, sha256) VALUES ('0000_retired.sql', 'x')"
        )
        try:
            with pytest.raises(MigrationChanged, match=r"0000_retired\.sql"):
                apply_all(conn)
        finally:
            conn.rollback()


def test_each_file_is_committed_before_the_next_runs(empty: Conn, tmp_path: Path) -> None:
    """新增的列舉值要先提交，後面的檔才能用；同一個交易裡建型別的全新資料庫測不出來。"""
    directory = _files(
        tmp_path / "m",
        **{
            "0001_type": "CREATE TYPE kind AS ENUM ('a');",
            "0002_value": "ALTER TYPE kind ADD VALUE 'b';",
            "0003_use": "CREATE TABLE t (k kind DEFAULT 'b');",
        },
    )

    assert apply_all(empty, directory) == ["0001_type.sql", "0002_value.sql", "0003_use.sql"]
