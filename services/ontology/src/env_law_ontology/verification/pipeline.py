"""驗證與接受：對尚未被接受的陳述跑閘門、記錄結果，再依政策升為 confirmed 或 rejected。

沒有人在這條線上。陳述是否成為推論用的事實，只看紀錄：必要的方法最近一次都通過、沒有任何方法最近
一次失敗（`annotation.settle`）。無法判定的閘門（例如委任的限值所依的陳述尚未被接受）會在下一輪
再跑：接受一批陳述可能讓另一批陳述的閘門變得可判定，所以流程重複到沒有進展為止。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from env_law_ontology.verification.gates import DEFAULT_GATES, Gate, GateResult
from env_law_ontology.verification.models import ModelError

Conn = psycopg.Connection[tuple[object, ...]]


@dataclass(frozen=True)
class Settled:
    confirmed: int
    rejected: int
    rounds: int


def verify(conn: Conn, gates: Sequence[Gate] = DEFAULT_GATES) -> int:
    """對每個 extracted 的陳述，把還沒有定論的閘門跑一遍並記錄；回傳新寫入的驗證列數。

    「定論」是這個方法最近一次為 pass、fail 或 not_applicable；inconclusive 與沒跑過的會（再）跑。
    """
    written = 0
    ids = [
        cast(int, row[0])
        for row in conn.execute(
            "SELECT id FROM annotation.statement WHERE review = 'extracted' ORDER BY id"
        )
    ]
    for sid in ids:
        for gate in gates:
            latest = conn.execute(
                "SELECT verdict::text FROM annotation.verification"
                " WHERE statement_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
                (sid, gate.method),
            ).fetchone()
            if latest is not None and latest[0] != "inconclusive":
                continue
            try:
                result = gate.check(conn, sid)
            except ModelError as e:
                # 模型沒回答不是標註的錯：記為無法判定，下一輪（或下次跑）再試，不駁回也不中斷。
                result = GateResult("inconclusive", {"error": str(e)[:300]})
            verdict, evidence = (
                ("not_applicable", {}) if result is None else (result.verdict, result.evidence)
            )
            conn.execute(
                "SELECT annotation.record_verification("
                "%s, %s, %s, %s::annotation.verdict, %s::jsonb)",
                (sid, gate.method, gate.verifier, verdict, Jsonb(evidence)),
            )
            written += 1
    conn.commit()
    return written


def settle(
    conn: Conn, required: Sequence[str], gates: Sequence[Gate] = DEFAULT_GATES, max_rounds: int = 5
) -> Settled:
    """驗證、接受，重複到沒有進展。回傳累計升為 confirmed 與 rejected 的數目。"""
    confirmed = rejected = rounds = 0
    for _ in range(max_rounds):
        rounds += 1
        verify(conn, gates)
        row = conn.execute(
            "SELECT confirmed, rejected FROM annotation.settle(%s)", (list(required),)
        ).fetchone()
        conn.commit()
        assert row is not None
        confirmed += cast(int, row[0])
        rejected += cast(int, row[1])
        if row[0] == 0 and row[1] == 0:
            break
    return Settled(confirmed, rejected, rounds)
