"""閘門一：往返驗證。標註 → 確定性描述 → 另一家族模型重抽 → 比對結構。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.verification import prompts
from envcite_ontology.verification.gates import GateResult, unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.model_gates.common import eligible, extractor_of
from envcite_ontology.verification.model_gates.render import (
    SQL_SANCTION,
    SanctionRow,
    render_annotation,
)
from envcite_ontology.verification.models import Model


def _signature(
    kind: str | None,
    deontic: str | None,
    conditions: Sequence[tuple[str, str, str, float | None, bool, bool]],
    sanction: tuple[str, float | None, float | None, bool, bool] | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "deontic": deontic,
        "conditions": sorted(conditions, key=repr),
        "sanction": sanction,
    }


class _StoredConditionRow(NamedTuple):
    role: str
    code: str
    comparator: str
    threshold: Decimal | None
    negated: bool
    needs_assessment: bool


_SQL_STORED_CONDITIONS = (
    "SELECT c.condition_role::text, k.code, c.comparator::text, c.threshold,"
    "       c.negated, c.needs_assessment"
    "  FROM annotation.statement_condition AS c"
    "  JOIN vocab.concept AS k ON k.id = c.concept_id"
    " WHERE c.statement_id = %s"
)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _stored_signature(conn: Conn, statement_id: int) -> dict[str, Any]:
    u = unit_of(conn, statement_id)
    conds = [
        (c.role, c.code, c.comparator, _float(c.threshold), c.negated, c.needs_assessment)
        for c in fetch(conn, _SQL_STORED_CONDITIONS, (statement_id,), _StoredConditionRow)
    ]
    row = fetch_one(conn, SQL_SANCTION, (statement_id,), SanctionRow)
    sanction = None
    if row is not None:
        sanction = (
            row.effect,
            _float(row.low),
            _float(row.high),
            row.per_occasion,
            row.discretionary,
        )
    return _signature(u.kind, u.deontic, conds, sanction)


def _answered_signature(answer: Mapping[str, Any]) -> dict[str, Any]:
    conds: list[tuple[str, str, str, float | None, bool, bool]] = []
    for c in answer.get("conditions") or []:
        threshold = c.get("threshold")
        conds.append(
            (
                str(c.get("role")),
                str(c.get("code")),
                str(c.get("comparator")),
                None if threshold is None else float(threshold),
                bool(c.get("negated", False)),
                bool(c.get("judgement", False)),
            )
        )
    s = answer.get("sanction")
    sanction = None
    if isinstance(s, Mapping):
        low, high = s.get("min"), s.get("max")
        sanction = (
            str(s.get("effect")),
            None if low is None else float(low),
            None if high is None else float(high),
            bool(s.get("repeating", False)),
            bool(s.get("discretionary", False)),
        )
    # 只有規範性陳述有義務方向（同資料庫限制與載入處理）。重抽者在授權、定義上寫 permission 是把
    # 「得」讀成許可，不是另一種結構；不在此丟掉，整類授權條款會因不對稱被擋下。
    kind = answer.get("kind")
    deontic = answer.get("deontic") if kind == "prescriptive" else None
    return _signature(kind, deontic, conds, sanction)


@dataclass(frozen=True)
class RoundtripGate:
    """標註 → 確定性描述 → 另一家族模型重抽 → 比對結構；重抽者看不到原文與原標註。

    種類、義務方向、要件、罰則逐項相同才通過。檢查的是標註可被還原，不是它與原文的距離
    （那是獨立測試與評審團的事）。
    """

    reformalizer: Model
    allow_same_family: bool = False
    method: str = "roundtrip"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含重抽者的名稱。"""
        return f"gate:roundtrip@1:{self.reformalizer.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """重抽者只看標註描述寫回的結構與標註逐項相同為 pass，有差異為 fail。

        沒有合格的重抽者或答案取不出 JSON 為 inconclusive；差異列在證據的 `differences`。
        """
        ok, skipped = eligible(
            [self.reformalizer], extractor_of(conn, statement_id), self.allow_same_family
        )
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        answer = object_from(
            ok[0].complete(prompts.reformalize(description=render_annotation(conn, statement_id)))
        )
        if answer is None:
            return GateResult("inconclusive", {"reason": "unparseable answer"})
        mine, theirs = _stored_signature(conn, statement_id), _answered_signature(answer)
        if mine == theirs:
            return GateResult("pass", {"verifier": ok[0].name})
        diff = {
            k: {"annotation": mine[k], "roundtrip": theirs[k]} for k in mine if mine[k] != theirs[k]
        }
        return GateResult("fail", {"verifier": ok[0].name, "differences": diff})
