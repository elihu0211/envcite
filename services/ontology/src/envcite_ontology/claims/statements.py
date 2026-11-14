"""陳述的寫入：陳述本身、角色槽位與條件；罰則的列由 `sanctions` 接手。"""

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from envcite_ontology.annotation_document import (
    Condition,
    Slot,
    Statement,
)
from envcite_ontology.claims.anchoring import anchor, provision
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.rows import new_claim, seal, span_range
from envcite_ontology.claims.vocabulary import resolve
from envcite_ontology.db import Conn


class Sanctioned(NamedTuple):
    """條件已寫、等所處罰的陳述封存後才寫罰則的陳述，與它已寫的條件數。"""

    statement: Statement
    conditions: int


class Written(NamedTuple):
    """陳述、時段與條件寫完後的狀態；`ids` 之後隨封存沿用既有主張而改動。"""

    ids: dict[str, int]
    conditions: int
    duplicates: int
    sanctioned: list[Sanctioned]


def seal_statement(conn: Conn, ids: dict[str, int], sid: str) -> bool:
    """封存陳述的主張；已有內容相同的主張而改指既有的（`ids` 隨之更新）則回 False。"""
    kept = seal(conn, ids[sid])
    if kept == ids[sid]:
        return True
    ids[sid] = kept
    return False


def write_statements(
    conn: Conn,
    statements: Sequence[Statement],
    concepts: Mapping[str, int],
    derivation_id: int,
    review: str,
) -> Written:
    """陳述連同其角色與條件；沒有罰則的當場封存，有罰則的留給 `write_sanctions`。"""
    ids: dict[str, int] = {}
    conditions = duplicates = 0
    sanctioned: list[Sanctioned] = []
    for s in statements:
        if s.id in ids:
            raise AnnotationError(f"陳述 id 重複：{s.id}")
        ids[s.id] = _statement(conn, s, derivation_id, review)
        _slots(conn, ids[s.id], s.slots, concepts)
        written = _conditions(conn, ids[s.id], s.conditions, concepts)
        if s.sanction is not None:
            # 罰則的內容含所處罰的陳述：等那一方封存、id 確定後再封存
            sanctioned.append(Sanctioned(s, written))
            continue
        if seal_statement(conn, ids, s.id):
            conditions += written
        else:
            duplicates += 1
    return Written(ids, conditions, duplicates, sanctioned)


def _statement(conn: Conn, s: Statement, derivation_id: int, review: str) -> int:
    provision_id, digest = anchor(conn, "陳述", s.id, s.law, s.path, s.text)
    sid = new_claim(conn, "statement", derivation_id, review)
    conn.execute(
        "INSERT INTO annotation.statement"
        " (id, provision_id, content_sha256, span, statement_kind, norm_modality)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (sid, provision_id, digest, span_range(s.span), s.kind, s.deontic),
    )
    return sid


def _slots(
    conn: Conn,
    statement_id: int,
    slots: Mapping[str, Slot | list[Slot]],
    concepts: Mapping[str, int],
) -> None:
    """一個角色一個值，或一串彼此擇一的值。"""
    for slot, value in slots.items():
        for alternative, v in enumerate([value] if isinstance(value, Slot) else value, 1):
            _slot(conn, statement_id, slot, alternative, v, concepts)


def _slot(
    conn: Conn,
    statement_id: int,
    slot: str,
    alternative: int,
    v: Slot,
    concepts: Mapping[str, int],
) -> None:
    conn.execute(
        "INSERT INTO annotation.statement_slot"
        " (statement_id, slot_role, alternative, concept_id, literal, liability_basis, unstated)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            statement_id,
            slot,
            alternative,
            resolve(concepts, v.concept) if v.concept is not None else None,
            v.literal,
            v.liability,
            v.unstated is True,
        ),
    )


def _conditions(
    conn: Conn, statement_id: int, conditions: Sequence[Condition], concepts: Mapping[str, int]
) -> int:
    for c in conditions:
        conn.execute(
            "INSERT INTO annotation.statement_condition"
            " (statement_id, condition_role, disjunct_no, concept_id, comparator,"
            "  threshold, measure_unit,"
            "  value_concept_id, negated, delegate_provision_id, needs_assessment,"
            "  delegate_permit_concept_id, measurement_method_concept_id,"
            "  averaging_period_concept_id, carried)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                statement_id,
                c.role,
                c.clause,
                resolve(concepts, c.concept),
                c.comparator,
                c.threshold,
                c.unit,
                resolve(concepts, c.value_concept) if c.value_concept else None,
                c.negated,
                provision(conn, c.delegate.law, c.delegate.path) if c.delegate else None,
                c.judgement,
                *(
                    resolve(concepts, code) if code else None
                    for code in (c.permit, c.method, c.averaging)
                ),
                c.carried is True,
            ),
        )
    return len(conditions)
