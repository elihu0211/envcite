"""閘門二：獨立測試。測試產生者只看原文，案例丟進推論程式比對。"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning import evaluate
from envcite_ontology.verification import prompts
from envcite_ontology.verification.gates import GateResult, Verdict, statement_as_of, unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.model_gates.common import eligible, extractor_of
from envcite_ontology.verification.models import Model

_VERDICTS = {
    "violation",
    "compliant",
    "not_applicable",
    "undetermined",
    "excluded",
    "conditional_violation",
    "conditional_compliant",
}


class _ConceptRow(NamedTuple):
    code: str
    label: str
    comparator: str
    threshold: Decimal | None
    unit: str | None


_SQL_CONCEPTS = (
    "SELECT k.code, k.name, c.comparator::text, c.threshold, c.measure_unit"
    "  FROM annotation.statement_condition AS c"
    "  JOIN vocab.concept AS k ON k.id = c.concept_id"
    " WHERE c.statement_id = %s ORDER BY k.code, c.id"
)


def _concept_line(conn: Conn, statement_id: int) -> list[str]:
    """給測試設計者的概念清單，附型別（取自標註的要件；無單位的 eq 1 是成立與否）。

    不說明型別，設計者會對成立與否的概念編出數值，那是型別歧義，不是標註錯。
    """
    lines: dict[str, tuple[str, set[str]]] = {}
    for concept in fetch(conn, _SQL_CONCEPTS, (statement_id,), _ConceptRow):
        presence = concept.comparator == "eq" and concept.threshold == 1 and not concept.unit
        kind = lines.setdefault(concept.code, (concept.label, set()))[1]
        kind.add(
            "成立與否，成立為 1、不成立為 0"
            if presence
            else f"數值{f'，單位 {concept.unit}' if concept.unit else ''}"
        )
    return [
        f"{code}：{label}（{'；'.join(sorted(kinds))}）" for code, (label, kinds) in lines.items()
    ]


@dataclass(frozen=True)
class IndependentTestGate:
    """另一個模型只讀原文設計案例與預期結論，案例丟進推論程式比對。

    全部一致才通過、全不一致為失敗，其餘無法判定（產生者與標註都可能錯）。
    只適用義務陳述：其他種類的結論不在推論程式的 verdict 裡。
    """

    tester: Model
    allow_same_family: bool = False
    method: str = "independent_test"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含測試設計者的名稱。"""
        return f"gate:independent_test@1:{self.tester.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """設計者的案例與推論程式的結論全一致為 pass、全不一致為 fail，其餘為 inconclusive。

        不是義務陳述回 None。沒有合格的設計者、沒有生效起日，或可用、可執行的案例各不足三個，
        都是 inconclusive。
        """
        u = unit_of(conn, statement_id)
        if not (u.kind == "prescriptive" and u.deontic == "obligation"):
            return None
        ok, skipped = eligible(
            [self.tester], extractor_of(conn, statement_id), self.allow_same_family
        )
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        as_of = statement_as_of(conn, statement_id)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        concepts = _concept_line(conn, statement_id)
        answer = object_from(ok[0].complete(prompts.write_tests(text=u.quoted, concepts=concepts)))
        cases = [
            c
            for c in (answer or {}).get("cases") or []
            if isinstance(c, Mapping) and c.get("expected") in _VERDICTS
        ]
        if len(cases) < 3:
            return GateResult("inconclusive", {"reason": "fewer than three usable cases"})
        results: list[dict[str, Any]] = []
        for c in cases:
            try:
                facts = {str(k): float(v) for k, v in dict(c.get("facts") or {}).items()}
            except TypeError, ValueError:
                continue
            got = evaluate(conn, as_of, facts, include_unsettled=True).verdicts.get(statement_id)
            results.append(
                {
                    "facts": facts,
                    "expected": c["expected"],
                    "got": got,
                    "agree": got == c["expected"],
                }
            )
        if len(results) < 3:
            return GateResult("inconclusive", {"reason": "fewer than three runnable cases"})
        agree = sum(r["agree"] for r in results)
        verdict: Verdict = (
            "pass" if agree == len(results) else ("fail" if agree == 0 else "inconclusive")
        )
        return GateResult(verdict, {"verifier": ok[0].name, "agree": agree, "cases": results})
