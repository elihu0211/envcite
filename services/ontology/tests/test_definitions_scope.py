"""定義的查詢只對「有定義的法規」解析時點，結果與對全部法規解析完全相同。

涵蓋兩條：推論讀的 `vocabulary_facts._DEFINITIONS`，
與回報出處的 `annotation_read._DEFINITION_SOURCES`。
事故背景與夾具見 `tests/scope_support.py`。
"""

from pathlib import Path

import psycopg
import pytest

from envcite_ontology.annotation_read import _DEFINITION_SOURCES
from envcite_ontology.reasoning.facts.vocabulary_facts import _DEFINITIONS
from envcite_ontology.verification import definition_gates as dg
from tests.model_gates_support import Scripted
from tests.scope_support import (
    FILLER_LAWS,
    add_filler_laws,
    difference,
    one,
    provision_lookups_by_resource,
    timed,
)
from tests.test_definition_verification import READER_OK, _extracted

Conn = psycopg.Connection[tuple[object, ...]]

#: 改寫前的兩條查詢，一字不改：對全部法規各解析一次（只用於比對與計時，生產環境不再執行）。
LEGACY_DEFINITIONS = """
    SELECT lr.id, tk.code, mk.code, t.id
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.definitions_at(lr.id, %(lang)s, %(on)s) AS d
      JOIN annotation.term_definition AS t ON t.id = d.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
      JOIN annotation.term_definition_member AS m ON m.definition_id = t.id
      JOIN vocab.concept AS mk ON mk.id = m.member_concept_id
     ORDER BY lr.id, tk.code, mk.code
"""

LEGACY_SOURCES = """
    SELECT t.id, corpus.version_iri(r.version_id), tk.name,
           coalesce((SELECT array_agg(mk.name ORDER BY mk.name)
                       FROM annotation.term_definition_member AS m
                       JOIN vocab.concept AS mk ON mk.id = m.member_concept_id
                      WHERE m.definition_id = t.id), '{}')
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.definitions_at(lr.id, %(language)s, %(on)s) AS r
      JOIN annotation.term_definition AS t ON t.id = r.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
     WHERE t.id = ANY(%(ids)s)
"""


@pytest.fixture
def defined_among_many(conn: Conn, tmp_path: Path) -> tuple[Conn, int]:
    """一部有一則已確認定義（兩個成員）的法規，混在 `FILLER_LAWS` 部沒有定義的法規之中。"""
    definition = _extracted(conn, tmp_path)
    settled = dg.settle(conn, dg.gates_for(Scripted("beta:reader", READER_OK)))
    assert (settled.confirmed, settled.rejected) == (1, 0)
    add_filler_laws(conn)
    return conn, definition


def _with_ids(sql: str, definition: int) -> str:
    """把 `ANY(%(ids)s)` 換成字面值，讓共用輔助（只帶時點參數）能執行計畫。"""
    return sql.replace("%(ids)s", f"ARRAY[{definition}]::bigint[]")


def test_the_scoped_facts_query_returns_exactly_the_rows_of_the_full_scan(
    defined_among_many: tuple[Conn, int],
) -> None:
    conn, _ = defined_among_many
    old = LEGACY_DEFINITIONS.replace("ORDER BY lr.id, tk.code, mk.code", "")
    new = _DEFINITIONS.replace("ORDER BY lr.id, tk.code, mk.code", "")

    assert difference(conn, old, new) == 0
    assert difference(conn, new, old) == 0
    # 兩邊同為空的比對不證明任何事：這份資料上要有兩列（兩個成員）。
    assert int(one(conn, f"SELECT count(*) FROM ({old}) AS d")) == 2


def test_the_scoped_source_query_returns_exactly_the_rows_of_the_full_scan(
    defined_among_many: tuple[Conn, int],
) -> None:
    conn, definition = defined_among_many
    old = _with_ids(LEGACY_SOURCES, definition)
    new = _with_ids(_DEFINITION_SOURCES, definition)

    assert difference(conn, old, new) == 0
    assert difference(conn, new, old) == 0
    assert int(one(conn, f"SELECT count(*) FROM ({old}) AS d")) == 1


def test_only_laws_with_definitions_are_resolved(defined_among_many: tuple[Conn, int]) -> None:
    conn, definition = defined_among_many
    laws = int(one(conn, "SELECT count(*) FROM corpus.legal_resource"))
    assert laws == FILLER_LAWS + 1

    for scoped, legacy in (
        (_DEFINITIONS, LEGACY_DEFINITIONS),
        (_with_ids(_DEFINITION_SOURCES, definition), _with_ids(LEGACY_SOURCES, definition)),
    ):
        assert provision_lookups_by_resource(conn, scoped) == 1
        # 舊查詢確實替每部法規各解析一次：這條斷言證明上面的計數看得見差別。
        assert provision_lookups_by_resource(conn, legacy) >= laws


def test_the_scoped_queries_stay_within_a_time_budget(
    defined_among_many: tuple[Conn, int],
) -> None:
    conn, definition = defined_among_many
    for sql in (_DEFINITIONS, _with_ids(_DEFINITION_SOURCES, definition)):
        seconds, rows = min((timed(conn, sql) for _ in range(3)), key=lambda t: t[0])

        assert rows
        assert seconds < 0.5, f"{seconds:.3f}s"
