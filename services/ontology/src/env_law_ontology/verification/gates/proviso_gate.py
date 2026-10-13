"""閘門四：原文有但書，標註裡就要有承接它的例外陳述。

往返驗證的重抽者只看標註導出的描述，沒看原文：漏掉的但書會在描述裡一起消失，所以必然通過。
這個閘門直接讀原文：條文（或它的上層項）有但書，同一條裡就要有至少一個例外陳述（`exception_of`），
否則不讓這個陳述被接受——標註可能漏了但書，也可能但書是目前的本體表達不了的樣子，兩者都不該
被當成已確認。

但書的樣子（《法規內容之編排體例》）：以「但」字開頭，表示例外、特加限制或解除限制；也有
「…不在此限」。「但法律另有規定者，從其規定」是保留規定，不是例外，不要求例外陳述。

限度：這個閘門只看有沒有承接，不看承接得對不對。效果面的但書（「但情節輕微者，得減輕或免除
其處罰」）要排除的是法律效果而不是整個陳述，目前的例外只能排除整個陳述；這類條文會停在
`inconclusive`，把本體的缺口顯出來，而不是靜默地丟掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from env_law_ontology.verification.gates.base import Conn, GateResult, unit_of

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
            "  JOIN corpus.provision AS pe ON pe.id = ex.provision_id"
            "  JOIN corpus.provision AS ps ON ps.id = %s"
            " WHERE pe.resource_id = ps.resource_id AND pe.article_path = ps.article_path"
            "   AND ex.confirm_state <> 'rejected'"
            " LIMIT 1",
            (u.provision_id,),
        ).fetchone()
        return GateResult(
            "pass" if covered else "inconclusive",
            {"provisos": found, "covered": covered is not None},
        )
