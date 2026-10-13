"""法律變更時的適用（行政罰法第 5 條）：行為後法律有變更，適用裁處時之法律；但裁處前之法律
有利於受處罰者，適用最有利於受處罰者之規定。

「最有利」不是機器能排序的（罰鍰額、處罰種類、要件各有高低），所以不替呼叫端選：列出行為日到
裁處日之間每一組不同的陳述，各自推論，由呼叫端依第 5 條挑。只有一組（期間內沒有任何帶陳述的
條文改變過效力）時沒有變更。

「不同」以陳述編號比：陳述錨定（單元，內容雜湊），文字沒變就是同一個陳述；效力列更替而文字
沒變不算變更，文字改了、條文失效或尚未施行，才換一組。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import psycopg

from env_law_ontology.reasoning.engine import Result, evaluate

Conn = psycopg.Connection[tuple[object, ...]]

_APPLICABLE = """
    SELECT r.statement_id
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.statement_versions_at(lr.id, %(lang)s, %(on)s) AS r
"""

# 帶陳述的單元其效力起訖、與法規廢止日：只有這些日子，適用的陳述組才可能改變。
_CHANGE_DATES = """
    SELECT d FROM (
        SELECT lower(v.force_period) AS d
          FROM corpus.provision_version AS v
         WHERE v.superseded_at IS NULL AND EXISTS (
               SELECT 1 FROM annotation.statement AS s WHERE s.provision_id = v.provision_id)
        UNION
        SELECT upper(v.force_period)
          FROM corpus.provision_version AS v
         WHERE v.superseded_at IS NULL AND EXISTS (
               SELECT 1 FROM annotation.statement AS s WHERE s.provision_id = v.provision_id)
        UNION
        SELECT repealed_on FROM corpus.legal_resource
    ) AS days
     WHERE d > %(conduct)s AND d <= %(decided)s
     ORDER BY d
"""


@dataclass(frozen=True)
class Candidate:
    """裁處前之法律的一組陳述與其推論。`as_of` 是這組陳述適用的第一天。"""

    as_of: date
    result: Result


@dataclass(frozen=True)
class Transition:
    """`at_decision` 是裁處時之法律（第 5 條的預設）；`earlier` 是行為日到裁處日之間、陳述組
    與裁處時不同的法律，依日期排序，可能為空。"""

    conduct_on: date
    decided_on: date
    at_decision: Result
    earlier: tuple[Candidate, ...]

    @property
    def changed(self) -> bool:
        return bool(self.earlier)


def _statement_ids(conn: Conn, language: str, day: date) -> frozenset[int]:
    return frozenset(
        int(row[0])  # type: ignore[call-overload]
        for row in conn.execute(_APPLICABLE, {"lang": language, "on": day})
    )


def evaluate_across(
    conn: Conn,
    conduct_on: date,
    decided_on: date,
    case: Mapping[str, float | Sequence[float]],
    language: str = "zho",
) -> Transition:
    """裁處時之法律，加上行為日到裁處日之間每一組不同的陳述；裁處權時效一律對裁處日。"""
    if conduct_on > decided_on:
        raise ValueError("conduct_on must not be after decided_on")
    days = [conduct_on]
    for row in conn.execute(_CHANGE_DATES, {"conduct": conduct_on, "decided": decided_on}):
        if isinstance(row[0], date):
            days.append(row[0])
    final = _statement_ids(conn, language, decided_on)
    seen = {final}
    earlier: list[Candidate] = []
    for day in days:
        ids = _statement_ids(conn, language, day)
        if ids in seen:
            continue
        seen.add(ids)
        earlier.append(Candidate(day, evaluate(conn, day, case, language, decided_on=decided_on)))
    return Transition(
        conduct_on,
        decided_on,
        evaluate(conn, decided_on, case, language),
        tuple(earlier),
    )
