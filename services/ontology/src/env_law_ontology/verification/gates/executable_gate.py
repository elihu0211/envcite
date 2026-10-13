"""閘門三：標註可被推論程式評估，且結論如要件所預期地翻轉。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from env_law_ontology.reasoning import Result, evaluate
from env_law_ontology.verification.gates.base import Conn, GateResult, Unit, unit_of
from env_law_ontology.verification.gates.conditions import (
    Cond,
    exception_facts,
    load_conditions,
    statement_as_of,
    unsupported,
)

# 義務內容成立、不成立時的結論，與 rules.lp 的 complies/2 同表；許可兩邊皆合規。
_POLARITY: dict[str, tuple[str, str]] = {
    "obligation": ("compliant", "violation"),
    "prohibition": ("violation", "compliant"),
    "permission": ("compliant", "compliant"),
}


@dataclass(frozen=True)
class ExecutableGate:
    """標註可被推論程式評估且結論如預期翻轉；只檢查標註與推論程式，不對照原文。

    規範性陳述：適用範圍任一要件不成立即不適用。
    罰則：所處罰陳述違反時，自身適用範圍成立列為候選、未知附條件、不成立不列。
    """

    method: str = "executable"
    verifier: str = "gate:executable@2"
    language: str = "zho"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = unit_of(conn, statement_id)
        if u.kind == "penalty":
            return self._penalty(conn, u)
        pair = _POLARITY.get(u.deontic or "")
        if u.kind != "prescriptive" or pair is None:
            return None
        ready = self._ready(conn, u)
        if isinstance(ready, GateResult):
            return ready
        as_of, conds, resolved, base = ready
        holds, fails = pair
        scope = [c for c in conds if c.role == "scope"]
        duty = [c for c in conds if c.role == "duty"]
        if not duty and holds != fails:
            return GateResult("inconclusive", {"reason": "no duty condition"})
        if not duty and not scope:
            return GateResult("inconclusive", {"reason": "no condition"})
        sat = self._vals(scope, True, resolved)
        cases: list[tuple[str, dict[str, float], str]] = []
        if duty:
            cases.append(("duty holds", base | sat | self._vals(duty, True, resolved), holds))
            cases.append(("duty broken", base | sat | self._vals(duty, False, resolved), fails))
        else:
            cases.append(("scope holds", base | sat, holds))
        duty_ok = self._vals(duty, holds == "compliant", resolved)
        for broken in scope:
            facts = (
                base
                | sat
                | duty_ok
                | {broken.code: float(broken.value(False, resolved[broken.id]))}
            )
            cases.append((f"{broken.code} not met", facts, "not_applicable"))
        return self._verdicts(conn, as_of, u.statement_id, cases)

    def _penalty(self, conn: Conn, u: Unit) -> GateResult:
        row = conn.execute(
            "SELECT punishes_id FROM annotation.sanction WHERE statement_id = %s",
            (u.statement_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return GateResult("inconclusive", {"reason": "penalty punishes nothing"})
        punished = unit_of(conn, cast(int, row[0]))
        pair = _POLARITY.get(punished.deontic or "")
        if punished.kind != "prescriptive" or pair is None or pair[0] == pair[1]:
            return GateResult("inconclusive", {"reason": "punished statement cannot be a breach"})
        breach_ready = self._ready(conn, punished)
        if isinstance(breach_ready, GateResult):
            return breach_ready
        _, pconds, presolved, pbase = breach_ready
        if not any(c.role == "duty" for c in pconds):
            return GateResult(
                "inconclusive", {"reason": "punished statement has no duty condition"}
            )
        pen_ready = self._ready(conn, u)
        if isinstance(pen_ready, GateResult):
            return pen_ready
        as_of, conds, resolved, base = pen_ready
        scope = [c for c in conds if c.role == "scope"]
        breach = (
            pbase
            | self._vals([c for c in pconds if c.role == "scope"], True, presolved)
            | self._vals([c for c in pconds if c.role == "duty"], pair[0] == "violation", presolved)
            | {"ap_intent": 1.0}
        )
        sat = self._vals(scope, True, resolved)
        held = base | breach | sat
        probe = evaluate(conn, as_of, held, self.language, include_unsettled=True)
        if probe.verdicts.get(punished.statement_id) != "violation":
            return GateResult(
                "inconclusive", {"reason": "breach facts did not violate the punished statement"}
            )
        cases: list[tuple[str, dict[str, float], str]] = [("scope holds", held, "candidate")]
        for broken in scope:
            facts = (
                base | breach | sat | {broken.code: float(broken.value(False, resolved[broken.id]))}
            )
            cases.append((f"{broken.code} not met", facts, "absent"))
        owned = next((c for c in scope if c.code not in breach), None)
        if owned is not None:
            unknown = {k: v for k, v in held.items() if k != owned.code}
            cases.append(("scope unknown", unknown, "conditional"))
        results: list[dict[str, Any]] = [
            {
                "case": "scope holds",
                "expected": "candidate",
                "got": self._penalty_state(probe, u.statement_id),
            }
        ]
        for name, facts, expected in cases[1:]:
            got = self._penalty_state(
                evaluate(conn, as_of, facts, self.language, include_unsettled=True),
                u.statement_id,
            )
            results.append({"case": name, "expected": expected, "got": got})
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    def _ready(
        self, conn: Conn, u: Unit
    ) -> GateResult | tuple[date, list[Cond], dict[int, Decimal], dict[str, float]]:
        as_of = statement_as_of(conn, u.statement_id)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        conds = load_conditions(conn, u.statement_id)
        problem = unsupported(conds)
        if problem:
            return GateResult("inconclusive", {"reason": problem})
        resolved: dict[int, Decimal] = {}
        for c in conds:
            value = c.threshold
            if value is None and c.delegated:
                row = conn.execute(
                    "SELECT annotation.threshold_at(%s, %s, %s)", (c.id, self.language, as_of)
                ).fetchone()
                value = None if row is None or row[0] is None else Decimal(str(row[0]))
            if value is None:
                return GateResult("inconclusive", {"reason": f"threshold of {c.code} unresolved"})
            resolved[c.id] = value
        return as_of, conds, resolved, exception_facts(conn, u.statement_id)

    def _verdicts(
        self,
        conn: Conn,
        as_of: date,
        statement_id: int,
        cases: list[tuple[str, dict[str, float], str]],
    ) -> GateResult:
        results: list[dict[str, Any]] = []
        for name, facts, expected in cases:
            result = evaluate(conn, as_of, facts, self.language, include_unsettled=True)
            results.append(
                {
                    "case": name,
                    "expected": expected,
                    "got": result.verdicts.get(statement_id),
                    "problems": [list(p) for p in result.problems],
                }
            )
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    @staticmethod
    def _vals(conds: list[Cond], truth: bool, resolved: dict[int, Decimal]) -> dict[str, float]:
        return {c.code: float(c.value(truth, resolved[c.id])) for c in conds}

    @staticmethod
    def _penalty_state(result: Result, statement_id: int) -> str:
        hit = [p for p in result.penalties if p.statement_id == statement_id]
        if not hit:
            return "absent"
        conditional = {p.conditional for p in hit}
        if len(conditional) != 1:
            return "both"
        return "conditional" if hit[0].conditional else "candidate"
