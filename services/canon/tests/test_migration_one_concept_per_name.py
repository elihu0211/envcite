"""0009：既有的同名概念合併成一列，封存的主張指紋重算，同名但用法不同的改名區分。

遷移改寫資料，所以要在一個只套到 0008 的獨立資料庫放進重複的概念，再套 0009。
"""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from canon.annotate import load
from canon.db import Conn
from canon.migrate import MIGRATIONS_DIR, apply_all
from canon.reasoning.engine import evaluate
from tests.reasoning_support import _plain_duty, _scope
from tests.test_annotation import BEFORE, _doc
from tests.test_environmental_annotation import _law
from tests.test_import import _scalar

_NAME = "化學需氧量"
_DB = "canon_migration_0009"
_TARGET = "0009_one_concept_per_name.sql"


@pytest.fixture
def staged(dsn: str, tmp_path: Path) -> Iterator[tuple[Conn, Path]]:
    """只套到 0008 的獨立資料庫，與放在暫存目錄、等著套的 0009。"""
    url = make_conninfo(dsn, dbname=_DB)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(_DB))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_DB)))
    before = tmp_path / "migrations"
    before.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if path.name < _TARGET:
            shutil.copy(path, before / path.name)
    try:
        with psycopg.connect(url) as conn:
            apply_all(conn, before)
            yield conn, before
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(_DB))
            )


def _duty(ident: str, path: str, concept: str, **condition: Any) -> dict[str, Any]:
    duty = _plain_duty(text=f"條{path[-1]}")
    duty["id"], duty["path"] = ident, path
    duty["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": concept,
        "comparator": "le",
        "threshold": 100,
    } | condition
    return duty


def _flag(ident: str, path: str, concept: str) -> dict[str, Any]:
    duty = _duty(ident, path, concept)
    duty["conditions"][1] = _scope(concept) | {"role": "duty"}
    return duty


def _seed(conn: Conn, tmp_path: Path) -> dict[str, int]:
    """同名重複的概念，各有已封存的主張，加上內容會在合併後相撞的兩份、沒人用的一個、用法不同的一對。"""
    _law(conn, tmp_path)
    doc = _doc(
        _duty("keep", "art_1", "cod"),
        _duty("same_text_twin", "art_1", "cod_b"),
        _duty("other_article", "art_2", "cod_c"),
        _flag("as_flag", "art_3", "stage_flag"),
        _duty("as_number", "art_4", "stage_number", unit="個月"),
    )
    for code, label in (
        ("cod_b", "二"),
        ("cod_c", "三"),
        ("cod_unused", "四"),
        ("stage_flag", "階段一"),
        ("stage_number", "階段二"),
    ):
        doc["concepts"].append({"code": code, "label": label})
    ids = dict(load(conn, doc).ids)
    # 載入器已不會產生同名的概念；改名重現舊資料裡的重複
    conn.execute(
        "UPDATE vocab.concept SET name = %s WHERE code IN ('cod_b', 'cod_c', 'cod_unused')",
        (_NAME,),
    )
    conn.execute("UPDATE vocab.concept SET name = '階段' WHERE code LIKE 'stage_%'")
    conn.commit()
    return ids


def _names(conn: Conn, *codes: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT code, name FROM vocab.concept WHERE code = ANY(%s)", (list(codes),)
    ).fetchall()
    return {str(code): str(name) for code, name in rows}


def test_same_name_concepts_become_one_row(staged: tuple[Conn, Path], tmp_path: Path) -> None:
    conn, before = staged
    _seed(conn, tmp_path)
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept WHERE name = %s", _NAME) == 4

    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    assert _scalar(conn, "SELECT count(*) FROM vocab.concept WHERE name = %s", _NAME) == 1
    kept = _scalar(conn, "SELECT code FROM vocab.concept WHERE name = %s", _NAME)
    assert kept == "cod"  # 最早建立的
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept WHERE code = 'cod_unused'") == 0


def test_a_fact_on_the_kept_concept_reaches_every_statement(
    staged: tuple[Conn, Path], tmp_path: Path
) -> None:
    conn, before = staged
    ids = _seed(conn, tmp_path)
    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    verdicts = evaluate(conn, BEFORE, {"discharging": 1.0, "cod": 150.0}).verdicts

    assert verdicts[ids["keep"]] == "violation"
    assert verdicts[ids["other_article"]] == "violation"


def test_fingerprints_match_the_merged_content(staged: tuple[Conn, Path], tmp_path: Path) -> None:
    conn, before = staged
    _seed(conn, tmp_path)
    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    stale = _scalar(
        conn,
        "SELECT count(*) FROM annotation.assertion AS a WHERE a.fingerprint IS NOT NULL"
        " AND a.fingerprint IS DISTINCT FROM annotation.assertion_fingerprint(a.id)",
    )
    assert stale == 0


def test_two_claims_that_became_identical_leave_one(
    staged: tuple[Conn, Path], tmp_path: Path
) -> None:
    conn, before = staged
    ids = _seed(conn, tmp_path)
    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    state = {
        k: _scalar(
            conn, "SELECT confirm_state::text FROM annotation.assertion WHERE id = %s", ids[k]
        )
        for k in ("keep", "same_text_twin")
    }
    assert state == {"keep": "confirmed", "same_text_twin": "rejected"}


def test_a_flag_and_a_number_of_one_name_stay_two_concepts(
    staged: tuple[Conn, Path], tmp_path: Path
) -> None:
    conn, before = staged
    _seed(conn, tmp_path)
    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    names = _names(conn, "stage_flag", "stage_number")

    assert names == {"stage_flag": "階段", "stage_number": "階段（個月）"}


def test_the_seal_still_holds_after_the_migration(
    staged: tuple[Conn, Path], tmp_path: Path
) -> None:
    conn, before = staged
    _seed(conn, tmp_path)
    shutil.copy(MIGRATIONS_DIR / _TARGET, before / _TARGET)
    apply_all(conn, before)

    with pytest.raises(psycopg.errors.CheckViolation, match="is sealed"):
        conn.execute(
            "UPDATE annotation.statement_condition SET negated = NOT negated"
            " WHERE condition_role = 'duty'"
        )
    conn.rollback()
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO vocab.concept (scheme_id, code, name)"
            " SELECT scheme_id, 'cod_again', name FROM vocab.concept WHERE code = 'cod'"
        )
