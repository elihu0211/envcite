"""驗證與接受：對未接受的陳述跑閘門並記錄，再依政策升為 confirmed 或 rejected。

接受只看紀錄（`annotation.settle`）：必要方法最近一次皆通過、無方法最近一次失敗。
接受一批可能讓另一批的無法判定閘門變得可判定，故重複到沒有進展。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from psycopg.types.json import Jsonb

from envcite_ontology.db import Conn
from envcite_ontology.verification.gates import DEFAULT_GATES, Gate, GateResult
from envcite_ontology.verification.models import ModelError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settled:
    """陳述的接受結果：累計升為 confirmed、rejected 的數目與跑了幾輪。"""

    confirmed: int
    rejected: int
    rounds: int


def verify(conn: Conn, gates: Sequence[Gate] = DEFAULT_GATES) -> int:
    """對每個 extracted 陳述跑尚無定論的閘門並記錄；回傳新寫入的驗證列數。

    最近一次非 inconclusive 即有定論。每個陳述跑完就 commit：模型呼叫昂貴，崩潰不丟答案且進度可見。
    """
    written = 0
    ids = [
        cast(int, row[0])
        for row in conn.execute(
            "SELECT id FROM annotation.assertion"
            " WHERE assertion_kind = 'statement' AND confirm_state = 'extracted' ORDER BY id"
        )
    ]
    for done, sid in enumerate(ids, 1):
        for gate in gates:
            latest = conn.execute(
                "SELECT result::text FROM annotation.verification"
                " WHERE assertion_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
                (sid, gate.method),
            ).fetchone()
            if latest is not None and latest[0] != "inconclusive":
                continue
            try:
                result = gate.check(conn, sid)
            except ModelError as e:
                # 模型沒回答不是標註的錯：記為無法判定，留待下次，不駁回也不中斷。
                result = GateResult("inconclusive", {"error": str(e)[:300]})
            verdict, evidence = (
                ("not_applicable", {}) if result is None else (result.verdict, result.evidence)
            )
            conn.execute(
                "SELECT annotation.record_verification("
                "%s, %s, %s, %s::annotation.gate_result, %s::jsonb)",
                (sid, gate.method, gate.verifier, verdict, Jsonb(evidence)),
            )
            written += 1
        conn.commit()
        if done % 10 == 0 or done == len(ids):
            log.info("驗證 %d/%d", done, len(ids))
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
            "SELECT confirmed, rejected FROM annotation.settle('statement', %s)", (list(required),)
        ).fetchone()
        conn.commit()
        assert row is not None
        confirmed += cast(int, row[0])
        rejected += cast(int, row[1])
        if row[0] == 0 and row[1] == 0:
            break
    return Settled(confirmed, rejected, rounds)
