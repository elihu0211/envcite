"""閘門一：罰則的額度與帶單位的門檻值，與原文的數字一致，且比較符號含不含本數與原文的詞一致。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from envcite_ontology.verification.gates.base import Conn, GateResult, Unit, Verdict, unit_of
from envcite_ontology.verification.gates.cn_number import (
    QUALIFIER_COMPARATOR,
    amounts,
    numbers_in,
    qualified_numbers,
    ranges,
)

_COMPLEMENT = {"ge": "lt", "lt": "ge", "le": "gt", "gt": "le", "eq": "ne", "ne": "eq"}


@dataclass(frozen=True)
class NumbersGate:
    """罰則的額度與帶單位的門檻值，與原文的數字一致。"""

    method: str = "numbers"
    verifier: str = "gate:numbers@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = unit_of(conn, statement_id)
        checks: list[dict[str, Any]] = []
        sanction = conn.execute(
            "SELECT amount_text, min_amount, max_amount FROM annotation.sanction"
            " WHERE statement_id = %s",
            (statement_id,),
        ).fetchone()
        if sanction is not None and any(v is not None for v in sanction):
            checks.extend(self._sanction(u, *sanction))
        for value, comparator, negated in conn.execute(
            "SELECT threshold, comparator::text, negated FROM annotation.statement_condition"
            " WHERE statement_id = %s AND threshold IS NOT NULL"
            " AND (cond_role = 'limit' OR measure_unit IS NOT NULL)",
            (statement_id,),
        ):
            have = numbers_in(u.quoted)
            ok = (
                int(Decimal(str(value))) in have
                if Decimal(str(value)) == int(Decimal(str(value)))
                else False
            )
            checks.append(
                {"what": "threshold", "value": str(value), "in_text": sorted(have), "ok": ok}
            )
            if ok:
                bound = self._bound(u, int(Decimal(str(value))), str(comparator), bool(negated))
                if bound is not None:
                    checks.append(bound)
        if not checks:
            return None
        verdict: Verdict = (
            "pass"
            if all(c["ok"] is True for c in checks)
            else ("fail" if any(c["ok"] is False for c in checks) else "inconclusive")
        )
        return GateResult(verdict, {"checks": checks})

    @staticmethod
    def _bound(u: Unit, value: int, comparator: str, negated: bool) -> dict[str, Any] | None:
        """比較符號含不含本數，與原文數字後的比較詞一致；任一比較詞相符即可，原文沒有比較詞則不檢查。"""
        words = [w for v, w in qualified_numbers(u.quoted) if v == value]
        if not words:
            return None
        effective = _COMPLEMENT[comparator] if negated else comparator
        allowed = {
            c
            for w in words
            for c in (QUALIFIER_COMPARATOR[w], _COMPLEMENT[QUALIFIER_COMPARATOR[w]])
        }
        return {
            "what": "bound",
            "value": str(value),
            "words": words,
            "comparator": effective,
            "ok": effective in allowed,
        }

    @staticmethod
    def _sanction(u: Unit, amount_text: Any, low: Any, high: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if amount_text is not None:
            out.append(
                {
                    "what": "amount_text",
                    "value": amount_text,
                    "ok": str(amount_text) in f"{u.context}\n{u.text}",
                }
            )
        found = amounts(u.with_context)
        if not found and (low is not None or high is not None):
            stored_amounts = [None if v is None else int(v) for v in (low, high)]
            out.append({"what": "amounts", "stored": stored_amounts, "text": [], "ok": False})
        stored = (None if low is None else int(low), None if high is None else int(high))
        # 原文可能分級（各有一組額度）：標註的下限與上限須取自同一組。
        tiers = ranges(found)
        want = next((t for t in tiers if t == stored), tiers[0] if tiers else (None, None))
        for name, have, wanted in (("min", stored[0], want[0]), ("max", stored[1], want[1])):
            if have is None and wanted is None:
                continue
            if have is None:
                out.append(
                    {"what": name, "stored": None, "text": wanted, "ok": None}
                )  # 原文有、標註無
            else:
                out.append({"what": name, "stored": have, "text": wanted, "ok": wanted == have})
        return out
