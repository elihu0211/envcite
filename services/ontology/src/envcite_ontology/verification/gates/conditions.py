"""可執行閘門讀取的要件、使要件成立或不成立的事實值、但書與評估日期。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from envcite_ontology.verification.gates.base import Conn

_RAW_TRUE = {"le": 0, "lt": -1, "ge": 0, "gt": 1, "eq": 0, "ne": 1}
_RAW_FALSE = {"le": 1, "lt": 0, "ge": -1, "gt": 0, "eq": 1, "ne": 0}


@dataclass(frozen=True)
class Cond:
    id: int
    role: str
    clause: int
    code: str
    comparator: str
    negated: bool
    threshold: Decimal | None
    delegated: bool
    by_concept: bool

    def value(self, truth: bool, resolved: Decimal) -> Decimal:
        """使此要件（含「非」）成立或不成立的事實值。"""
        raw_true = truth != self.negated
        delta = (_RAW_TRUE if raw_true else _RAW_FALSE)[self.comparator]
        return resolved + delta


def statement_as_of(conn: Conn, statement_id: int) -> date | None:
    """評估日期：陳述所錨定現行版本的最近生效起日；沒有則 None。"""
    row = conn.execute(
        "SELECT max(lower(v.force_period)) FROM annotation.statement AS s"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE s.id = %s",
        (statement_id,),
    ).fetchone()
    return None if row is None or row[0] is None else cast(date, row[0])


def load_conditions(conn: Conn, statement_id: int) -> list[Cond]:
    rows = conn.execute(
        "SELECT c.id, c.condition_role::text, c.disjunct_no, k.code, c.comparator::text, c.negated,"
        "       c.threshold, c.delegate_provision_id IS NOT NULL,"
        "       c.value_concept_id IS NOT NULL"
        "  FROM annotation.statement_condition AS c"
        "  JOIN vocab.concept AS k ON k.id = c.concept_id"
        " WHERE c.statement_id = %s AND c.condition_role <> 'limit' ORDER BY c.id",
        (statement_id,),
    ).fetchall()
    return [
        Cond(
            cast(int, r[0]),
            cast(str, r[1]),
            cast(int, r[2]),
            cast(str, r[3]),
            cast(str, r[4]),
            bool(r[5]),
            None if r[6] is None else Decimal(str(r[6])),
            bool(r[7]),
            bool(r[8]),
        )
        for r in rows
    ]


def unsupported(conds: list[Cond]) -> str | None:
    """推論程式評估不了的要件形狀；回原因，可評估則 None。"""
    if any(c.by_concept for c in conds):
        return "a condition compares concept values"
    for role in ("scope", "duty"):
        if len({c.clause for c in conds if c.role == role}) > 1:
            return f"{role} has several clauses (disjunction)"
    codes = [c.code for c in conds]
    if len(codes) != len(set(codes)):
        return "two conditions share one concept"
    return None


def exception_facts(conn: Conn, statement_id: int) -> dict[str, float]:
    """但書的事實值：令其要件不成立，陳述才不被排除。"""
    facts: dict[str, float] = {}
    rows = conn.execute(
        "SELECT c.comparator::text, c.negated, c.threshold, k.code"
        "  FROM annotation.statement_exception AS e"
        "  JOIN annotation.statement_condition AS c"
        "    ON c.statement_id = e.exception_id AND c.condition_role = 'scope'"
        "  JOIN vocab.concept AS k ON k.id = c.concept_id"
        " WHERE e.excluded_id = %s",
        (statement_id,),
    ).fetchall()
    for comparator, negated, threshold, code in rows:
        if threshold is None:
            continue
        c = Cond(
            0,
            "scope",
            1,
            cast(str, code),
            cast(str, comparator),
            bool(negated),
            None,
            False,
            False,
        )
        facts[c.code] = float(c.value(False, Decimal(str(threshold))))
    return facts
