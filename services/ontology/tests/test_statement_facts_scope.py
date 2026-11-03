"""`statement_facts` 的陳述查詢只對「有陳述的法規」解析時點，結果與對全部法規解析完全相同。

事故背景與夾具見 `tests/scope_support.py`。斷言：結果與舊查詢逐列相同（EXCEPT 兩個方向都是零）；
執行計畫只替有陳述的法規解析；耗時在預算內。
"""

from pathlib import Path
from typing import LiteralString

import psycopg
import pytest

from envcite_ontology.annotate import load
from envcite_ontology.reasoning.facts import _STATEMENTS, statement_facts
from tests.scope_support import (
    FILLER_LAWS,
    ON,
    add_filler_laws,
    difference,
    one,
    provision_lookups_by_resource,
    timed,
)
from tests.test_annotation import _doc, _duty, _limit
from tests.test_incremental import _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]

#: 改寫前的查詢，一字不改：對全部法規各解析一次（只用於比對與計時，生產環境不再執行）。
LEGACY = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id),
           lr.id
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.{resolver}(lr.id, %(lang)s, %(on)s) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""

RESOLVERS: tuple[LiteralString, ...] = ("anchored_statements_at", "statement_versions_at")


@pytest.fixture
def annotated_among_many(conn: Conn, tmp_path: Path) -> Conn:
    """一部有兩則已確認陳述的法規，混在 `FILLER_LAWS` 部沒有陳述的法規之中。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))
    add_filler_laws(conn)
    return conn


@pytest.mark.parametrize("resolver", RESOLVERS)
def test_the_scoped_query_returns_exactly_the_rows_of_the_full_scan(
    annotated_among_many: Conn, resolver: LiteralString
) -> None:
    conn = annotated_among_many
    old = LEGACY.format(resolver=resolver).replace("ORDER BY s.id", "")
    new = _STATEMENTS.format(resolver=resolver).replace("ORDER BY s.id", "")

    assert difference(conn, old, new) == 0
    assert difference(conn, new, old) == 0
    # 兩邊同為空的比對不證明任何事：兩個解析函式在這份資料上都要有列。
    assert int(one(conn, f"SELECT count(*) FROM ({old}) AS d")) == 2


def test_only_laws_with_statements_are_resolved(annotated_among_many: Conn) -> None:
    conn = annotated_among_many
    laws = int(one(conn, "SELECT count(*) FROM corpus.legal_resource"))
    annotated = int(
        one(
            conn,
            "SELECT count(DISTINCT p.resource_id) FROM annotation.statement AS x"
            "  JOIN corpus.provision AS p ON p.id = x.provision_id",
        )
    )
    assert (laws, annotated) == (FILLER_LAWS + 1, 1)

    scoped = provision_lookups_by_resource(
        conn, _STATEMENTS.format(resolver="anchored_statements_at")
    )
    full_scan = provision_lookups_by_resource(
        conn, LEGACY.format(resolver="anchored_statements_at")
    )

    assert scoped == annotated
    assert full_scan >= laws  # 舊查詢確實替每部法規各解析一次：這條斷言證明上面的計數看得見差別


def test_the_scoped_query_stays_within_a_time_budget(annotated_among_many: Conn) -> None:
    sql = _STATEMENTS.format(resolver="anchored_statements_at")

    seconds, rows = min((timed(annotated_among_many, sql) for _ in range(3)), key=lambda t: t[0])

    assert len(rows) == 2
    assert seconds < 0.5, f"{seconds:.3f}s"


def test_statement_facts_for_the_gate_is_unchanged_among_many_laws(
    annotated_among_many: Conn,
) -> None:
    """可執行性閘門（`--law O0040010` 流程中卡住的那一步）所走的入口，內容不受填充法規影響。

    不斷言耗時：整個入口還包含其他尚未限縮的查詢，牆鐘時間在負載高的 CI 上不穩定；
    效能由上面的計畫計數斷言保證。
    """
    facts = statement_facts(annotated_among_many, ON, include_unsettled=True).program

    assert facts.count("\nstatement(") + facts.startswith("statement(") == 2
