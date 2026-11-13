"""閘門三：標註可被推論程式評估，且結論如要件所預期地翻轉。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from envcite_ontology.db import Conn
from envcite_ontology.reasoning import Result, evaluate
from envcite_ontology.reasoning.facts import CaseValue, Measurement, limit_at
from envcite_ontology.verification.gates.base import GateResult, Unit, unit_of
from envcite_ontology.verification.gates.conditions import (
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

#: 委由個別處分的要件：許可不在語料裡，以一個假設的許可量值檢查結論是否翻轉。
_PERMITTED = Decimal(100)


@dataclass(frozen=True)
class _Ready:
    as_of: date
    conds: list[Cond]
    resolved: dict[int, Decimal]
    base: dict[str, CaseValue]
    measurements: dict[str, list[Measurement]]
    permits: dict[tuple[str, str], list[float]]


@dataclass(frozen=True)
class ExecutableGate:
    """標註可被推論程式評估且結論如預期翻轉；只檢查標註與推論程式，不對照原文。

    規範性陳述：適用範圍任一要件不成立即不適用。
    罰則：所處罰陳述違反時，自身適用範圍成立列為候選、未知附條件、不成立不列。
    """

    method: str = "executable"
    verifier: str = "gate:executable@3"
    language: str = "zho"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = unit_of(conn, statement_id)
        if u.kind == "sanction":
            return self._penalty(conn, u)
        pair = _POLARITY.get(u.deontic or "")
        if u.kind != "prescriptive" or pair is None:
            return None
        ready = self._ready(conn, u)
        if isinstance(ready, GateResult):
            return ready
        conds, resolved, base = ready.conds, ready.resolved, ready.base
        holds, fails = pair
        scope = [c for c in conds if c.role == "scope"]
        duty = [c for c in conds if c.role == "duty"]
        if not duty and holds != fails:
            return GateResult("inconclusive", {"reason": "no duty condition"})
        if not duty and not scope:
            return GateResult("inconclusive", {"reason": "no condition"})
        sat = self._vals(scope, True, resolved)
        cases: list[tuple[str, dict[str, CaseValue], str]] = []
        if duty:
            cases.append(("duty holds", base | sat | self._vals(duty, True, resolved), holds))
            cases.append(("duty broken", base | sat | self._vals(duty, False, resolved), fails))
        else:
            cases.append(("scope holds", base | sat, holds))
        duty_ok = self._vals(duty, holds == "compliant", resolved)
        for broken in scope:
            facts = base | sat | duty_ok | {broken.code: broken.fact(False, resolved[broken.id])}
            cases.append((f"{broken.code} not met", facts, "not_applicable"))
        return self._verdicts(conn, ready, u.statement_id, cases)

    def _penalty(self, conn: Conn, u: Unit) -> GateResult:
        row = conn.execute(
            "SELECT punished_statement_id FROM annotation.sanction WHERE statement_id = %s",
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
        pconds, presolved, pbase = breach_ready.conds, breach_ready.resolved, breach_ready.base
        if not any(c.role == "duty" for c in pconds):
            return GateResult(
                "inconclusive", {"reason": "punished statement has no duty condition"}
            )
        pen_ready = self._ready(conn, u)
        if isinstance(pen_ready, GateResult):
            return pen_ready
        as_of, conds, resolved, base = (
            pen_ready.as_of,
            pen_ready.conds,
            pen_ready.resolved,
            pen_ready.base,
        )
        scope = [c for c in conds if c.role == "scope"]
        breach = (
            pbase
            | self._vals([c for c in pconds if c.role == "scope"], True, presolved)
            | self._vals([c for c in pconds if c.role == "duty"], pair[0] == "violation", presolved)
            | {"ap_intent": 1.0}
        )
        # 以另一個效果為前提的罰則：前提也要列出。取第一個前提，使它的適用範圍成立；前提處罰的
        # 是別的陳述（例如跨法的環境講習）時，這裡造不出案件。
        inputs = [breach_ready, pen_ready]
        trigger = conn.execute(
            "SELECT t.trigger_statement_id, n.punished_statement_id,"
            "       EXISTS (SELECT 1 FROM annotation.sanction_trigger AS tt"
            "                WHERE tt.statement_id = t.trigger_statement_id)"
            "  FROM annotation.sanction_trigger AS t"
            "  JOIN annotation.sanction AS n ON n.statement_id = t.trigger_statement_id"
            " WHERE t.statement_id = %s ORDER BY t.trigger_statement_id LIMIT 1",
            (u.statement_id,),
        ).fetchone()
        if trigger is not None:
            if trigger[1] != punished.statement_id:
                return GateResult("inconclusive", {"reason": "trigger punishes another statement"})
            if trigger[2]:
                return GateResult("inconclusive", {"reason": "trigger has a trigger of its own"})
            trigger_ready = self._ready(conn, unit_of(conn, cast(int, trigger[0])))
            if isinstance(trigger_ready, GateResult):
                return trigger_ready
            inputs.append(trigger_ready)
            breach |= trigger_ready.base | self._vals(
                [c for c in trigger_ready.conds if c.role == "scope"],
                True,
                trigger_ready.resolved,
            )
        merged = _merge(as_of, inputs)
        sat = self._vals(scope, True, resolved)
        held = base | breach | sat
        probe = self._evaluate(conn, merged, held)
        if probe.verdicts.get(punished.statement_id) != "violation":
            return GateResult(
                "inconclusive", {"reason": "breach facts did not violate the punished statement"}
            )
        cases: list[tuple[str, dict[str, CaseValue], str]] = [("scope holds", held, "candidate")]
        for broken in scope:
            facts = base | breach | sat | {broken.code: broken.fact(False, resolved[broken.id])}
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
            got = self._penalty_state(self._evaluate(conn, merged, facts), u.statement_id)
            results.append({"case": name, "expected": expected, "got": got})
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    def _evaluate(self, conn: Conn, ready: _Ready, facts: dict[str, CaseValue]) -> Result:
        return evaluate(
            conn,
            ready.as_of,
            facts,
            self.language,
            include_unsettled=True,
            measurements=ready.measurements,
            permits=ready.permits,
        )

    def _ready(self, conn: Conn, u: Unit) -> GateResult | _Ready:
        as_of = statement_as_of(conn, u.statement_id)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        conds = load_conditions(conn, u.statement_id)
        problem = unsupported(conds)
        if problem:
            return GateResult("inconclusive", {"reason": problem})
        resolved: dict[int, Decimal] = {}
        measurements: dict[str, list[Measurement]] = {}
        permits: dict[tuple[str, str], list[float]] = {}
        for c in conds:
            if c.by_concept:
                resolved[c.id] = Decimal(0)  # 概念值要件沒有門檻值，事實值由概念代碼決定
                continue
            if c.permit is not None:
                resolved[c.id] = _PERMITTED
                permits[(c.permit, c.code)] = [float(_PERMITTED)]
                continue
            value = c.threshold
            if value is None and c.delegated:
                # 事實照限值的認定方式給，才比得到數值
                limit = limit_at(conn, c.id, self.language, as_of)
                if limit is not None:
                    value = limit.threshold
                    if limit.method is not None or limit.averaging is not None:
                        measurements[c.code] = [Measurement(limit.method, limit.averaging)]
            if value is None:
                return GateResult("inconclusive", {"reason": f"threshold of {c.code} unresolved"})
            resolved[c.id] = value
        return _Ready(
            as_of, conds, resolved, exception_facts(conn, u.statement_id), measurements, permits
        )

    def _verdicts(
        self,
        conn: Conn,
        ready: _Ready,
        statement_id: int,
        cases: list[tuple[str, dict[str, CaseValue], str]],
    ) -> GateResult:
        as_of = ready.as_of
        results: list[dict[str, Any]] = []
        for name, facts, expected in cases:
            result = self._evaluate(conn, ready, facts)
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
    def _vals(conds: list[Cond], truth: bool, resolved: dict[int, Decimal]) -> dict[str, CaseValue]:
        return {c.code: c.fact(truth, resolved[c.id]) for c in conds}

    @staticmethod
    def _penalty_state(result: Result, statement_id: int) -> str:
        hit = [p for p in result.penalties if p.statement_id == statement_id]
        if not hit:
            return "absent"
        conditional = {p.conditional for p in hit}
        if len(conditional) != 1:
            return "both"
        return "conditional" if hit[0].conditional else "candidate"


def _merge(as_of: date, inputs: list[_Ready]) -> _Ready:
    """數個陳述的認定方式與許可量值併在一起，供同一次推論使用（評估日取罰則的）。"""
    measurements: dict[str, list[Measurement]] = {}
    permits: dict[tuple[str, str], list[float]] = {}
    for r in inputs:
        measurements |= r.measurements
        permits |= r.permits
    return _Ready(as_of, [], {}, {}, measurements, permits)
