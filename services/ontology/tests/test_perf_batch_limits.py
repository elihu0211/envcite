"""驗證推論事實批次查詢不退化成 N+1 round-trip。"""

from datetime import date
from decimal import Decimal

import pytest

from envcite_ontology.reasoning import facts


def test_condition_facts_batches_delegated_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    conds = [
        facts._ConditionRow(
            id=100 + i,
            statement_id=1,
            role="obligation",
            disjunct=1,
            concept=f"c_{i}",
            comparator="gt",
            negated=False,
            needs_assessment=False,
            threshold=None,
            delegated=True,
            value_concept=None,
            permit=None,
        )
        for i in range(20)
    ]
    limits = [
        facts._DelegatedLimitRow(
            condition_id=100 + i,
            threshold=Decimal("10.5"),
            statement_id=200 + i,
            method=None,
            averaging=None,
        )
        for i in range(15)
    ]
    void_rows = [facts._VoidLimitRow(condition_id=119)]

    def fake_fetch(conn: object, sql: str, params: object, row_type: object) -> list[object]:
        calls.append(sql)
        if row_type is facts._ConditionRow:
            return list(conds)
        if row_type is facts._DelegatedLimitRow:
            return list(limits)
        if row_type is facts._VoidLimitRow:
            return list(void_rows)
        raise AssertionError(f"unexpected row_type: {row_type}")

    monkeypatch.setattr(facts, "fetch", fake_fetch)

    lines = facts._condition_facts(
        object(),  # type: ignore[arg-type]
        {"ids": [1]},
        "zh-Hant",
        date(2026, 11, 14),
    )

    assert len(calls) == 3
    assert "limit_cand(100,200,10500)." in lines
    assert "void_limit(119)." in lines
