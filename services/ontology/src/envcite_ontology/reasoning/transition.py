"""法律變更時的適用（行政罰法第 5 條）：行為後法律有變更，適用裁處時之法律；但裁處前之法律
有利於受處罰者，適用最有利於受處罰者之規定。

「最有利」無法機器排序（罰鍰額、處罰種類、要件各有高低），故不替呼叫端選：列出行為日到裁處日
之間每一組不同的陳述並各自推論，由呼叫端依第 5 條挑；只有一組即無變更。

「不同」以陳述編號比：陳述錨定（單元，內容雜湊），文字沒變就是同一個陳述；效力列更替而文字
沒變不算變更，文字改了、條文失效或尚未施行，才換一組。

**語料的歷史是有起點的。** 全國法規資料庫只有現行全文，匯入器只為實際匯入的版本建表達；更早的
公布只留生命週期事件。行為日早於語料持有某部法規的第一個版本（而該法規在那天已經制定）時，
那天的陳述組裡就沒有這部法規：看起來像「行為時沒有這些規定」，實際上是「語料沒有那個版本」。
兩者不能混為一談，所以每一組都帶 `uncovered_laws`，列出這種法規。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

import psycopg

from envcite_ontology.reasoning.engine import Result, evaluate
from envcite_ontology.reasoning.facts import Case, Measurements, Permits

Conn = psycopg.Connection[tuple[object, ...]]

_APPLICABLE = """
    SELECT r.statement_id
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.statement AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.statement_versions_at(lr.id, %(lang)s, %(on)s)
                         OFFSET 0) AS r
"""
# 只對有陳述的法規解析時點，且以 `OFFSET 0` 擋住函式內聯後的重排：與 `facts.py` 的 `_STATEMENTS`
# 同一個原因（內聯後規劃器排出對 `provision_version` 的二次方巢狀迴圈），理由與數字見那裡。
# 這條每個變更日跑一次，成本會乘上日數。

# 帶陳述單元的效力起訖、法規廢止日與違憲失效日：適用的陳述組只可能在這些日子改變。
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
        UNION
        SELECT invalid_from FROM corpus.ruling_invalidation
    ) AS days
     WHERE d > %(conduct)s AND d <= %(decided)s
     ORDER BY d
"""


#: 在 `on` 那天已經制定、但語料持有的第一個版本晚於那天的法規（限於提供陳述的法規）。
_UNCOVERED = """
    SELECT lr.local_id
      FROM corpus.legal_resource AS lr
     WHERE lr.id_scheme = 'moj_pcode'
       AND lr.id IN (SELECT p.resource_id
                       FROM annotation.statement AS s
                       JOIN corpus.provision AS p ON p.id = s.provision_id
                      WHERE s.id = ANY(%(ids)s))
       AND (SELECT min(e.version_date) FROM corpus.legal_expression AS e
             WHERE e.resource_id = lr.id AND e.language = %(lang)s) > %(on)s
       AND coalesce((SELECT min(le.event_date) FROM corpus.lifecycle_event AS le
                      WHERE le.resource_id = lr.id AND le.event_kind = 'enactment'),
                    %(on)s) <= %(on)s
     ORDER BY lr.local_id
"""


@dataclass(frozen=True)
class Candidate:
    """裁處前之法律的一組陳述與其推論。`as_of` 是這組陳述適用的第一天。

    `uncovered_laws`：那天已制定、但語料沒有當時版本的法規（pcode）。它們的規定不在這組陳述
    裡，不代表當時沒有規定。
    """

    as_of: date
    result: Result
    uncovered_laws: tuple[str, ...] = ()


@dataclass(frozen=True)
class Transition:
    """`at_decision` 為第 5 條預設的裁處時之法律；`earlier` 為期間內陳述組不同者，依日期排序。"""

    conduct_on: date
    decided_on: date
    at_decision: Result
    earlier: tuple[Candidate, ...]
    at_decision_uncovered_laws: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.earlier)


def _statement_ids(conn: Conn, language: str, day: date) -> frozenset[int]:
    return frozenset(
        int(row[0])  # type: ignore[call-overload]
        for row in conn.execute(_APPLICABLE, {"lang": language, "on": day})
    )


def _uncovered(conn: Conn, ids: frozenset[int], language: str, day: date) -> tuple[str, ...]:
    # 用裁處時的陳述組所屬的法規：早期才有、現在已沒有陳述的法規，與這次推論無關。
    rows = conn.execute(_UNCOVERED, {"ids": sorted(ids), "lang": language, "on": day})
    return tuple(str(r[0]) for r in rows)


def evaluate_across(
    conn: Conn,
    conduct_on: date,
    decided_on: date,
    case: Case,
    language: str = "zho",
    *,
    measurements: Measurements | None = None,
    permits: Permits | None = None,
    actor_bases: Collection[str] | None = None,
) -> Transition:
    """裁處時之法律，加行為日到裁處日之間每組不同的陳述；裁處權時效一律對裁處日。"""
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
        earlier.append(
            Candidate(
                day,
                evaluate(
                    conn,
                    day,
                    case,
                    language,
                    decided_on=decided_on,
                    measurements=measurements,
                    permits=permits,
                    actor_bases=actor_bases,
                ),
                _uncovered(conn, final, language, day),
            )
        )
    return Transition(
        conduct_on,
        decided_on,
        evaluate(
            conn,
            decided_on,
            case,
            language,
            measurements=measurements,
            permits=permits,
            actor_bases=actor_bases,
        ),
        tuple(earlier),
        _uncovered(conn, final, language, decided_on),
    )
