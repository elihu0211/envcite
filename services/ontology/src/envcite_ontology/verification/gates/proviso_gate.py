"""閘門四：原文有但書，標註裡就要有承接它的例外陳述。

條文（或上層項）有但書，同條內就要有至少一個例外陳述（`exception_of`）；往返驗證看不到原文，漏掉的但書會一起消失。
「但法律另有規定者，從其規定」是保留規定，不要求例外陳述。

只看有沒有承接，不看承接得對不對；排除法律效果的但書，目前的例外表達不了，會停在 `inconclusive`。
"""

import re
from dataclasses import dataclass

from envcite_ontology.db import Conn
from envcite_ontology.verification.gates.base import GateResult, unit_of

#: 以「但」開頭的句子（句首、段首、分號之後），與「不在此限」。
_PROVISO = re.compile(r"(?:^|[。；\n])\s*(但[^。]*。?)|([^。；\n]*不在此限)")

#: 保留規定：「但法律另有規定者，從其規定」。
_RESERVATION = re.compile(r"另有(?:規定|約定)者.{0,6}(?:從其規定|依其規定|不在此限)")


def provisos(text: str) -> list[str]:
    """原文中的但書句（不含保留規定）。"""
    out: list[str] = []
    for m in _PROVISO.finditer(text):
        sentence = (m.group(1) or m.group(2)).strip()
        if sentence and not _RESERVATION.search(sentence):
            out.append(sentence)
    return out


@dataclass(frozen=True)
class ProvisoGate:
    """原文有但書時，同一條裡要有例外陳述。"""

    method: str = "provisos"
    verifier: str = "gate:provisos@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = unit_of(conn, statement_id)
        if conn.execute(
            "SELECT 1 FROM annotation.statement_exception WHERE exception_id = %s", (statement_id,)
        ).fetchone():
            return None  # 這個陳述自己就是但書
        found = provisos(u.with_context)
        if not found:
            return None
        covered = conn.execute(
            "SELECT 1"
            "  FROM annotation.statement_exception AS e"
            "  JOIN annotation.statement AS ex ON ex.id = e.exception_id"
            "  JOIN annotation.assertion AS exc ON exc.id = ex.id"
            "  JOIN corpus.provision AS pe ON pe.id = ex.provision_id"
            "  JOIN corpus.provision AS ps ON ps.id = %s"
            " WHERE pe.resource_id = ps.resource_id AND pe.article_path = ps.article_path"
            "   AND exc.confirm_state <> 'rejected'"
            " LIMIT 1",
            (u.provision_id,),
        ).fetchone()
        return GateResult(
            "pass" if covered else "inconclusive",
            {"provisos": found, "covered": covered is not None},
        )
