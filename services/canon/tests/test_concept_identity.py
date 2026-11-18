"""概念的身分是（方案, 名稱）：同名即同一個概念，載入器沿用既有的，資料庫不允許兩列同名。"""

from pathlib import Path
from typing import Any

import psycopg
import pytest

from canon.annotate import AnnotationError, load
from canon.db import Conn
from canon.reasoning.engine import evaluate
from tests.reasoning_support import _plain_duty
from tests.test_annotation import BEFORE, _doc
from tests.test_environmental_annotation import _law
from tests.test_import import _scalar

_LABEL = "化學需氧量"


def _second_duty(concept: str) -> dict[str, Any]:
    """條 2 的義務，與條 1 的 `_plain_duty` 相同，只是限值概念換了一個代碼。"""
    duty = _plain_duty(text="條2")
    duty["id"] = "duty2"
    duty["path"] = "art_2"
    duty["conditions"][1]["concept"] = concept
    return duty


def _concepts_named(conn: Conn, name: str) -> int:
    return int(_scalar(conn, "SELECT count(*) FROM vocab.concept WHERE name = %s", name))


def test_a_second_code_for_a_known_name_reuses_the_concept(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    doc = _doc(_plain_duty(text="條1"), _second_duty("cod_again"))
    doc["concepts"].append({"code": "cod_again", "label": _LABEL})

    loaded = load(conn, doc)

    assert _concepts_named(conn, _LABEL) == 1
    used = _scalar(
        conn,
        "SELECT count(DISTINCT concept_id) FROM annotation.statement_condition"
        " WHERE condition_role = 'duty'",
    )
    assert used == 1
    verdicts = evaluate(conn, BEFORE, {"discharging": 1.0, "cod": 150.0}).verdicts
    assert [verdicts[loaded.ids[i]] for i in ("duty", "duty2")] == ["violation", "violation"]


def test_a_later_document_reuses_the_concept_too(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _doc(_plain_duty(text="條1")))
    doc = _doc(_second_duty("cod_again"))
    doc["concepts"].append({"code": "cod_again", "label": _LABEL})

    load(conn, doc)

    assert _concepts_named(conn, _LABEL) == 1


def test_a_concept_cannot_be_renamed_to_a_name_another_concept_has(
    conn: Conn, tmp_path: Path
) -> None:
    _law(conn, tmp_path)
    load(conn, _doc(_plain_duty(text="條1")))
    doc = _doc(_plain_duty(text="條1"))
    doc["concepts"][1]["label"] = "排放廢水"  # `cod` 改叫 `discharging` 的名稱

    with pytest.raises(AnnotationError, match="這個名稱已是概念「discharging」"):
        load(conn, doc)


def test_a_concept_can_be_renamed_to_a_free_name(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _doc(_plain_duty(text="條1")))
    doc = _doc(_plain_duty(text="條1"))
    doc["concepts"][1]["label"] = "化學需氧量（COD）"

    load(conn, doc)

    assert _concepts_named(conn, "化學需氧量（COD）") == 1
    assert _concepts_named(conn, _LABEL) == 0


def test_the_database_refuses_two_concepts_with_one_name(conn: Conn, tmp_path: Path) -> None:
    _law(conn, tmp_path)
    load(conn, _doc(_plain_duty(text="條1")))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO vocab.concept (scheme_id, code, name)"
            " SELECT scheme_id, 'cod_dup', name FROM vocab.concept WHERE code = 'cod'"
        )
