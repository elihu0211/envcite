"""閘門二：罰則所處罰的陳述，必須是原文引用的那一條（項）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from envcite_ontology.verification.gates.base import Conn, GateResult, unit_of
from envcite_ontology.verification.gates.cn_number import NUMBER, cn_number

_CITE = re.compile(rf"第([{NUMBER}]+)條(?:之([{NUMBER}]+))?(?:第([{NUMBER}]+)項)?")


def citations(text: str) -> list[str]:
    """原文引用的條（項）路徑：「第七條第一項」→ art_7/para_1、「第五條之一」→ art_5-1。"""
    out: list[str] = []
    for m in _CITE.finditer(text):
        try:
            path = f"art_{cn_number(m.group(1))}"
            if m.group(2):
                path += f"-{cn_number(m.group(2))}"
            if m.group(3):
                path += f"/para_{cn_number(m.group(3))}"
        except ValueError:
            continue
        out.append(path)
    return out


@dataclass(frozen=True)
class ReferencesGate:
    """所處罰陳述的條（項）須為原文引用，且該陳述須已接受。

    處罰對象仍 `extracted` 時無法判定、留待下一輪；已 rejected 則失敗。
    """

    method: str = "references"
    verifier: str = "gate:references@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        row = conn.execute(
            "SELECT pp.path, tc.confirm_state::text FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS t ON t.id = sn.punished_statement_id"
            "  JOIN annotation.assertion AS tc ON tc.id = t.id"
            "  JOIN corpus.provision AS pp ON pp.id = t.provision_id"
            " WHERE sn.statement_id = %s",
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        u = unit_of(conn, statement_id)
        target = str(row[0])
        cited = citations(u.text)
        covers = [c for c in cited if target == c or target.startswith(c + "/")]
        in_corpus = {
            str(p)
            for (p,) in conn.execute(
                "SELECT path FROM corpus.provision WHERE resource_id = %s AND path = ANY(%s)",
                (u.resource_id, cited),
            )
        }
        evidence = {
            "target": target,
            "cited": cited,
            # 語料沒有的引用只記錄，不算錯
            "unresolved": [c for c in cited if c not in in_corpus],
        }
        if not covers:
            return GateResult("fail", evidence)
        if row[1] == "rejected":
            return GateResult("fail", {**evidence, "reason": "the punished statement was rejected"})
        if row[1] == "extracted":
            return GateResult(
                "inconclusive",
                {**evidence, "reason": "the punished statement is not yet confirmed"},
            )
        return GateResult("pass", evidence)
