"""可執行閘門讀取的要件、使要件成立或不成立的事實值、但書與評估日期。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from envcite_ontology.db import Conn
from envcite_ontology.reasoning.facts import CaseValue

_RAW_TRUE = {"le": 0, "lt": -1, "ge": 0, "gt": 1, "eq": 0, "ne": 1}
_RAW_FALSE = {"le": 1, "lt": 0, "ge": -1, "gt": 0, "eq": 1, "ne": 0}

# 不屬於任何用語的概念值：與概念比較的要件取它時，「屬於」不成立（沒有定義會涵蓋它）。
NOT_A_MEMBER = "__not_a_member__"


@dataclass(frozen=True)
class Cond:
    """陳述的一個要件：對某概念的比較，及使它成立或不成立所需的事實值。"""

    id: int
    role: str
    #: 析取子句編號；同一角色有數個子句即析取
    clause: int
    code: str
    comparator: str
    negated: bool
    threshold: Decimal | None
    delegated: bool
    #: 與概念值比較的要件所比較的概念代碼；數值要件為 None
    value_code: str | None
    #: 委由個別處分的要件所委由的許可種類代碼；其餘為 None
    permit: str | None = None

    @property
    def by_concept(self) -> bool:
        """是否為與概念值比較的要件，而不是數值要件。"""
        return self.value_code is not None

    def value(self, truth: bool, resolved: Decimal) -> Decimal:
        """使此要件（含「非」）成立或不成立的事實值。"""
        raw_true = truth != self.negated
        delta = (_RAW_TRUE if raw_true else _RAW_FALSE)[self.comparator]
        return resolved + delta

    def fact(self, truth: bool, resolved: Decimal) -> CaseValue:
        """使此要件成立或不成立的案件事實：數值要件給數字，概念值要件給概念代碼。

        eq 的「屬於」成立取比較的概念本身（概念屬於自己，不需定義），不成立取不屬於任何用語的值；
        ne 相反。含「非」時再翻轉。
        """
        if self.value_code is None:
            return float(self.value(truth, resolved))
        member = (truth != self.negated) == (self.comparator == "eq")
        return self.value_code if member else NOT_A_MEMBER


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
    """陳述的適用範圍與義務內容要件，依編號排序；限值（`limit`）不含在內。"""
    rows = conn.execute(
        "SELECT id, condition_role, disjunct_no, concept_code, comparator, negated,"
        "       threshold, delegated, value_code, permit_code"
        "  FROM annotation.conditions_of(ARRAY[%s]::bigint[])"
        " WHERE condition_role <> 'limit' ORDER BY id",
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
            None if r[8] is None else cast(str, r[8]),
            None if r[9] is None else cast(str, r[9]),
        )
        for r in rows
    ]


def unsupported(conds: list[Cond]) -> str | None:
    """推論程式評估不了的要件形狀；回原因，可評估則 None。"""
    for role in ("scope", "duty"):
        if len({c.clause for c in conds if c.role == role}) > 1:
            return f"{role} has several clauses (disjunction)"
    codes = [c.code for c in conds]
    if len(codes) != len(set(codes)):
        return "two conditions share one concept"
    return None


def exception_facts(conn: Conn, statement_id: int) -> dict[str, CaseValue]:
    """但書的事實值：令其要件不成立，陳述才不被排除。"""
    facts: dict[str, CaseValue] = {}
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
            None,
        )
        facts[c.code] = float(c.value(False, Decimal(str(threshold))))
    return facts
