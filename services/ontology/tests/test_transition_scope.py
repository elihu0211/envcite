"""法律變更時的適用：每個變更日取「當天適用的陳述組」只對有陳述的法規解析，結果與對全部法規解析相同。

事故背景與夾具見 `tests/scope_support.py`。
"""

from datetime import date
from pathlib import Path

import psycopg
import pytest

from envcite_ontology.reasoning.transition import _APPLICABLE, _statement_ids
from tests.scope_support import (
    FILLER_LAWS,
    ON,
    add_filler_laws,
    difference,
    one,
    provision_lookups_by_resource,
    timed,
)
from tests.test_reasoning_transition import DECIDED, _amended

Conn = psycopg.Connection[tuple[object, ...]]

#: 改寫前的查詢，一字不改：對全部法規各解析一次（只用於比對與計時，生產環境不再執行）。
LEGACY = """
    SELECT r.statement_id
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.statement_versions_at(lr.id, %(lang)s, %(on)s) AS r
"""

#: 行為日早於語料（空組）、第一版生效中（限值 100）、修正後（限值 80）。
DAYS = (date(2010, 1, 1), ON, DECIDED)


@pytest.fixture
def amended_among_many(conn: Conn, tmp_path: Path) -> Conn:
    """一部有修正前後兩組陳述的法規，混在 `FILLER_LAWS` 部沒有陳述的法規之中。"""
    _amended(conn, tmp_path)
    add_filler_laws(conn)
    return conn


def test_the_scoped_query_returns_exactly_the_rows_of_the_full_scan(
    amended_among_many: Conn,
) -> None:
    conn = amended_among_many

    assert difference(conn, LEGACY, _APPLICABLE) == 0
    assert difference(conn, _APPLICABLE, LEGACY) == 0
    # 兩邊同為空的比對不證明任何事。
    assert int(one(conn, f"SELECT count(*) FROM ({LEGACY}) AS d")) == 2


@pytest.mark.parametrize("day", DAYS)
def test_the_statement_set_of_every_day_is_unchanged(amended_among_many: Conn, day: date) -> None:
    conn = amended_among_many
    old = {
        int(row[0])  # type: ignore[call-overload]
        for row in conn.execute(LEGACY, {"lang": "zho", "on": day})
    }

    assert _statement_ids(conn, "zho", day) == old


def test_only_laws_with_statements_are_resolved(amended_among_many: Conn) -> None:
    conn = amended_among_many
    laws = int(one(conn, "SELECT count(*) FROM corpus.legal_resource"))
    assert laws == FILLER_LAWS + 1

    scoped = provision_lookups_by_resource(conn, _APPLICABLE)
    full_scan = provision_lookups_by_resource(conn, LEGACY)

    # 只為有標註的那一部解析：次數是那一部的版本列數，與填充法規的部數無關。
    assert 0 < scoped * 100 < full_scan
    assert full_scan >= laws  # 這條斷言證明上面的計數看得見差別


def test_the_scoped_query_stays_within_a_time_budget(amended_among_many: Conn) -> None:
    seconds, rows = min(
        (timed(amended_among_many, _APPLICABLE) for _ in range(3)), key=lambda t: t[0]
    )

    assert len(rows) == 2
    assert seconds < 0.5, f"{seconds:.3f}s"
