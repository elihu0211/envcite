"""罰則的寫入：罰則的列與罰則前提，在所處罰的陳述封存、id 確定之後才寫。"""

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from envcite_ontology.claims.links import ref
from envcite_ontology.claims.statements import Sanctioned, seal_statement
from envcite_ontology.db import Conn


class SanctionsWritten(NamedTuple):
    """罰則寫完後的計數：新增的罰則、條件，與沿用既有主張的數目。"""

    sanctions: int
    conditions: int
    duplicates: int


def write_sanctions(
    conn: Conn, sanctioned: Sequence[Sanctioned], ids: dict[str, int]
) -> SanctionsWritten:
    """罰則的列，寫完即封存；`ids` 隨沿用既有主張而更新。"""
    sanctions = conditions = duplicates = 0
    for s, written in sanctioned:
        sanction = s.sanction
        assert sanction is not None  # `write_statements` 只把有罰則的放進來
        conn.execute(
            "INSERT INTO annotation.sanction"
            " (statement_id, punished_statement_id, legal_effect, amount_text,"
            "  min_amount, max_amount, per_occasion, discretionary)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ids[s.id],
                sanction.punishes_id
                if sanction.punishes_id is not None
                else ref(ids, str(sanction.punishes)),
                sanction.effect,
                sanction.amount_text,
                sanction.min,
                sanction.max,
                sanction.repeating,
                sanction.discretionary,
            ),
        )
        if seal_statement(conn, ids, s.id):
            sanctions, conditions = sanctions + 1, conditions + written
        else:
            duplicates += 1
    return SanctionsWritten(sanctions, conditions, duplicates)


def link_triggers(conn: Conn, sanctioned: Sequence[Sanctioned], ids: Mapping[str, int]) -> int:
    """新增的罰則前提數。"""
    triggers = 0
    for s, _ in sanctioned:
        assert s.sanction is not None
        for t in s.sanction.triggers or []:
            triggers += conn.execute(
                "INSERT INTO annotation.sanction_trigger"
                " (statement_id, trigger_statement_id, trigger_kind)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    ids[s.id],
                    t.statement_id if t.statement_id is not None else ref(ids, str(t.statement)),
                    t.kind,
                ),
            ).rowcount
    return triggers
