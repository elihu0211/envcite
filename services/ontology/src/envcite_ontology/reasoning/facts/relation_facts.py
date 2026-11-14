"""陳述的結構事實：行為與主體槽位、但書與優先、罰則與罰則前提。"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.facts.asp import q

_ACTIONS = """
    SELECT statement_id, coalesce(concept_code, literal)
      FROM annotation.slots_of(%(ids)s::bigint[])
     WHERE slot_role = 'action'
"""


class _ActionRow(NamedTuple):
    statement_id: int
    action: str


# 主體槽位的責任基礎：每個擇一的值一列；沒有標註的值（NULL）使主體不受責任基礎限制。
_SUBJECTS = """
    SELECT statement_id, liability_basis
      FROM annotation.slots_of(%(ids)s::bigint[])
     WHERE slot_role = 'subject'
"""


class _SubjectRow(NamedTuple):
    statement_id: int
    basis: str | None


_EXCEPTIONS = """
    SELECT exception_id, excluded_id FROM annotation.statement_exception
     WHERE exception_id = ANY(%(ids)s) AND excluded_id = ANY(%(ids)s)
"""


class _ExceptionRow(NamedTuple):
    exception: int
    excluded: int


_OVERRIDES = """
    SELECT prevailing_id, subordinate_id, priority_basis::text FROM annotation.statement_override
     WHERE prevailing_id = ANY(%(ids)s) AND subordinate_id = ANY(%(ids)s)
"""


class _OverrideRow(NamedTuple):
    winner: int
    loser: int
    basis: str


# 準用關係（annotation.mutatis_mutandis）。
_MUTATIS = """
    SELECT source_statement_id, target_statement_id
      FROM annotation.mutatis_mutandis
     WHERE source_statement_id = ANY(%(ids)s) AND target_statement_id = ANY(%(ids)s)
     ORDER BY source_statement_id, target_statement_id
"""


class _MutatisRow(NamedTuple):
    source: int
    target: int


def slot_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    """行為與主體槽位。"""
    out = [
        f"action({act.statement_id},{q(act.action)})."
        for act in fetch(conn, _ACTIONS, ids_param, _ActionRow)
    ]
    for subj in fetch(conn, _SUBJECTS, ids_param, _SubjectRow):
        out.append(
            f"subject_basis({subj.statement_id},{subj.basis})."
            if subj.basis is not None
            else f"subject_any({subj.statement_id})."
        )
    return out


def relation_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    """陳述之間的但書與優先關係。"""
    out = [
        f"exception({exc.exception},{exc.excluded})."
        for exc in fetch(conn, _EXCEPTIONS, ids_param, _ExceptionRow)
    ]
    out += [
        f"override({ovr.winner},{ovr.loser},{ovr.basis})."
        for ovr in fetch(conn, _OVERRIDES, ids_param, _OverrideRow)
    ]
    out += [
        f"mutatis_mutandis({m.source},{m.target})."
        for m in fetch(conn, _MUTATIS, ids_param, _MutatisRow)
    ]
    return out


_SANCTIONS = """
    SELECT statement_id, punished_statement_id, legal_effect, min_amount, max_amount,
           per_occasion
      FROM annotation.sanctions_of(%(ids)s::bigint[])
     WHERE punished_statement_id = ANY(%(ids)s)
"""


class SanctionRow(NamedTuple):
    """一個罰則的列：所處罰的陳述、效果與額度。"""

    statement_id: int
    punishes: int
    effect: str
    min_amount: Decimal | None
    max_amount: Decimal | None
    per_occasion: bool


def fetch_sanctions(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[SanctionRow]:
    """這些陳述的罰則列。"""
    return fetch(conn, _SANCTIONS, ids_param, SanctionRow)


def sanction_lines(sanctions: Sequence[SanctionRow]) -> list[str]:
    """罰則與按次處罰的事實。"""
    out: list[str] = []
    for sanc in sanctions:
        lo = "none" if sanc.min_amount is None else str(int(sanc.min_amount))
        hi = "none" if sanc.max_amount is None else str(int(sanc.max_amount))
        out.append(f"sanction({sanc.statement_id},{sanc.punishes},{sanc.effect},{lo},{hi}).")
        if sanc.per_occasion:
            out.append(f"repeating({sanc.statement_id}).")
    return out


# 被觸發者不論前提是否在當日的陳述裡都標出：前提不在（未確認、文字已改、法規未施行）就不列。
_TRIGGERS = """
    SELECT statement_id, trigger_statement_id, trigger_kind::text,
           trigger_statement_id = ANY(%(ids)s)
      FROM annotation.sanction_trigger
     WHERE statement_id = ANY(%(ids)s)
"""


class _TriggerRow(NamedTuple):
    statement_id: int
    trigger: int
    kind: str
    present: bool


def trigger_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    """罰則前提的事實。"""
    out: list[str] = []
    for trig in fetch(conn, _TRIGGERS, ids_param, _TriggerRow):
        out.append(f"triggered({trig.statement_id}).")
        if trig.present:
            out.append(f"trigger({trig.statement_id},{trig.trigger},{trig.kind}).")
    return out
