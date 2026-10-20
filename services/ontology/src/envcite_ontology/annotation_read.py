"""標註的讀取：某日有效、已確認的陳述，與推論結果引用的陳述之出處。

解析（哪一天、哪個版本列、是否已確認）留在 SQL 的 `annotation.statement_versions_at`，這裡不重做：
和推論用的是同一個函式，所以 `ListStatements` 看到的陳述與 `Evaluate` 用的陳述不會分岔。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from datetime import date
from decimal import Decimal
from typing import cast

from envcite.ontology.v1 import reasoning_pb2 as pb
from envcite_ontology import reasoning_codec as codec
from envcite_ontology.store import LANGUAGE, Conn

_STATEMENTS = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id)
      FROM annotation.statement_versions_at(%(resource)s, %(language)s, %(on)s) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""

_SLOTS = """
    SELECT sl.statement_id, sl.slot_role::text, k.code, sl.literal
      FROM annotation.statement_slot AS sl
      LEFT JOIN vocab.concept AS k ON k.id = sl.concept_id
     WHERE sl.statement_id = ANY(%(ids)s)
     ORDER BY sl.statement_id, sl.slot_role
"""

_CONDITIONS = """
    SELECT c.id, c.statement_id, c.condition_role::text, c.disjunct_no, k.code, c.comparator::text,
           c.threshold, c.measure_unit, c.negated, c.needs_assessment,
           vk.code, dl.local_id, dp.path
      FROM annotation.statement_condition AS c
      JOIN vocab.concept AS k ON k.id = c.concept_id
      LEFT JOIN vocab.concept AS vk ON vk.id = c.value_concept_id
      LEFT JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      LEFT JOIN corpus.legal_resource AS dl ON dl.id = dp.resource_id
     WHERE c.statement_id = ANY(%(ids)s)
     ORDER BY c.statement_id, c.condition_role, c.disjunct_no, c.id
"""

_EXCEPTIONS = """
    SELECT exception_id, excluded_id FROM annotation.statement_exception
     WHERE exception_id = ANY(%(ids)s)
     ORDER BY exception_id, excluded_id
"""

_OVERRIDES = """
    SELECT prevailing_id, subordinate_id, priority_basis::text FROM annotation.statement_override
     WHERE prevailing_id = ANY(%(ids)s) AND subordinate_id = ANY(%(ids)s)
     ORDER BY prevailing_id, subordinate_id
"""

_SANCTIONS = """
    SELECT statement_id, punished_statement_id, legal_effect::text, amount_text,
           min_amount, max_amount, per_occasion, discretionary
      FROM annotation.sanction
     WHERE statement_id = ANY(%(ids)s)
"""

#: 結果引用的陳述於某日的出處：只查這些陳述所屬的法規，不掃全部法規。
_SOURCES = """
    SELECT r.statement_id, corpus.version_iri(r.version_id)
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.statement_versions_at(lr.id, %(language)s, %(on)s) AS r
     WHERE lr.id IN (SELECT p.resource_id
                       FROM annotation.statement AS s
                       JOIN corpus.provision AS p ON p.id = s.provision_id
                      WHERE s.id = ANY(%(ids)s))
       AND r.statement_id = ANY(%(ids)s)
"""


_DEFINITION_SOURCES = """
    SELECT t.id, corpus.version_iri(r.version_id), tk.name,
           coalesce((SELECT array_agg(mk.name ORDER BY mk.name)
                       FROM annotation.term_definition_member AS m
                       JOIN vocab.concept AS mk ON mk.id = m.member_concept_id
                      WHERE m.definition_id = t.id), '{}')
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.definitions_at(lr.id, %(language)s, %(on)s) AS r
      JOIN annotation.term_definition AS t ON t.id = r.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
     WHERE t.id = ANY(%(ids)s)
"""


def definition_sources(
    conn: Conn, definition_ids: Collection[int], on: date
) -> dict[int, tuple[str, str, list[str]]]:
    """定義於 `on` 的出處：（版本 IRI，用語，成員），與推論讀的是同一個 `definitions_at`。"""
    if not definition_ids:
        return {}
    rows = conn.execute(
        _DEFINITION_SOURCES, {"language": LANGUAGE, "on": on, "ids": sorted(definition_ids)}
    ).fetchall()
    return {
        cast(int, did): (str(iri), str(term), [str(m) for m in cast("list[str]", members)])
        for did, iri, term, members in rows
    }


def list_statements(conn: Conn, resource_id: int, on: date | None) -> pb.ListStatementsResponse:
    """資源 `resource_id` 在 `on`（None 為今日）有效、已確認的陳述。"""
    params = {"resource": resource_id, "language": LANGUAGE, "on": on or date.today()}
    rows = conn.execute(_STATEMENTS, params).fetchall()
    ids = [cast(int, r[0]) for r in rows]
    if not ids:
        return pb.ListStatementsResponse()
    by_id = {"ids": ids}

    statements: dict[int, pb.Statement] = {}
    for sid, kind, modality, iri in rows:
        statement = pb.Statement(
            id=cast(int, sid), kind=codec.STATEMENT_KIND[str(kind)], version_iri=str(iri)
        )
        if modality is not None:
            statement.modality = codec.NORM_MODALITY[str(modality)]
        statements[cast(int, sid)] = statement

    for sid, slot, code, literal in conn.execute(_SLOTS, by_id):
        out = statements[cast(int, sid)].slots.add(role=codec.SLOT_ROLE[str(slot)])
        if code is not None:
            out.concept = str(code)
        else:
            out.literal = str(literal)

    _conditions(conn, by_id, statements)

    for exception, excluded in conn.execute(_EXCEPTIONS, by_id):
        statements[cast(int, exception)].exception_of.append(cast(int, excluded))
    for sid, punishes, effect, text, low, high, per_occasion, discretionary in conn.execute(
        _SANCTIONS, by_id
    ):
        sanction = statements[cast(int, sid)].sanction
        sanction.punishes = cast(int, punishes)
        sanction.effect = codec.LEGAL_EFFECT[str(effect)]
        sanction.amount_text = str(text or "")
        if low is not None:
            sanction.min_amount = int(cast(Decimal, low))
        if high is not None:
            sanction.max_amount = int(cast(Decimal, high))
        sanction.per_occasion = bool(per_occasion)
        sanction.discretionary = bool(discretionary)

    return pb.ListStatementsResponse(
        statements=list(statements.values()),
        overrides=[
            pb.Override(
                prevailing=cast(int, w),
                subordinate=cast(int, s),
                basis=codec.OVERRIDE_BASIS[str(b)],
            )
            for w, s, b in conn.execute(_OVERRIDES, by_id)
        ],
    )


def _conditions(
    conn: Conn, by_id: dict[str, list[int]], statements: dict[int, pb.Statement]
) -> None:
    """要件依（陳述, 角色）分組，組內依析取編號成為 DNF。"""
    grouped: dict[tuple[int, str], dict[int, list[pb.Condition]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in conn.execute(_CONDITIONS, by_id):
        cid, sid, role, disjunct, code, comparator, threshold, unit, negated, needs = row[:10]
        value_concept, delegate_law, delegate_path = row[10:]
        condition = pb.Condition(
            id=cast(int, cid),
            concept=str(code),
            comparator=codec.COMPARATOR[str(comparator)],
            unit=str(unit or ""),
            negated=bool(negated),
            needs_assessment=bool(needs),
        )
        if threshold is not None:
            condition.value.CopyFrom(codec.decimal(cast(Decimal, threshold)))
        elif value_concept is not None:
            condition.value_concept = str(value_concept)
        elif delegate_path is not None:
            condition.delegate.CopyFrom(
                pb.Delegation(law_id=str(delegate_law), provision_path=str(delegate_path))
            )
        grouped[(cast(int, sid), str(role))][cast(int, disjunct)].append(condition)

    for (sid, role), disjuncts in grouped.items():
        statement = statements[sid]
        if role == "limit":
            statement.limits.extend(c for group in disjuncts.values() for c in group)
            continue
        requirement = statement.scope if role == "scope" else statement.duty
        for _, conditions in sorted(disjuncts.items()):
            requirement.any_of.add(all_of=conditions)


def sources(conn: Conn, statement_ids: Collection[int], on: date) -> dict[int, str]:
    """陳述於 `on` 的出處（版本 IRI）。"""
    if not statement_ids:
        return {}
    rows = conn.execute(
        _SOURCES, {"language": LANGUAGE, "on": on, "ids": sorted(statement_ids)}
    ).fetchall()
    return {cast(int, sid): str(iri) for sid, iri in rows}
