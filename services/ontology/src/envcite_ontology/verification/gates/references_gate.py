"""閘門二：罰則所處罰的陳述，必須是原文引用的那一條（項）。"""

from dataclasses import dataclass

from envcite_ontology.db import Conn
from envcite_ontology.verification.gates.base import GateResult, unit_of
from envcite_ontology.violation import citations, within


@dataclass(frozen=True)
class ReferencesGate:
    """所處罰陳述的條（項）須為原文引用。

    所處罰的陳述是否已確認由綱要與 `annotation.settle` 強制，不在這裡判。
    """

    method: str = "references"
    verifier: str = "gate:references@2"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        row = conn.execute(
            "SELECT pp.path FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS t ON t.id = sn.punished_statement_id"
            "  JOIN corpus.provision AS pp ON pp.id = t.provision_id"
            " WHERE sn.statement_id = %s",
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        u = unit_of(conn, statement_id)
        target = str(row[0])
        cited = citations(u.text)
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
        # 罰則與受罰的義務同單元、原文不引別條（「…應…；違反者，處…」）時，不必引用自己
        own = not cited and target == u.path
        return GateResult("pass" if own or within(target, cited) else "fail", evidence)
