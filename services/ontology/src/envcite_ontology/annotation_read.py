"""標註的讀取：某日有效、已確認的陳述，與推論結果引用的陳述之出處。

解析（哪一天、哪個版本列、是否已確認）留在 SQL 的 `annotation.statement_versions_at`，這裡不重做：
和推論用的是同一個函式，所以 `ListStatements` 看到的陳述與 `Evaluate` 用的陳述不會分岔。
每個查詢的列有一個具名型別，欄位的型別與可否為 NULL 只在那裡宣告（次序同 SELECT）。
"""

from collections import defaultdict
from collections.abc import Collection
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from envcite.ontology.v2 import ontology_pb2 as ont_pb
from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.facts.statements import Invalidated
from envcite_ontology.reasoning_codec import enums
from envcite_ontology.reasoning_codec import outcome as outcome_codec
from envcite_ontology.store.records import LANGUAGE

_STATEMENTS = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id)
      FROM annotation.statement_versions_at(%(resource)s, %(language)s, %(on)s) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""

_INVALIDATED = """
    SELECT statement_id, corpus.version_iri(version_id), ruling_no, invalid_from, partial
      FROM annotation.invalidated_statements_at(%(resource)s, %(language)s, %(on)s)
"""

_SLOTS = """
    SELECT statement_id, slot_role, concept_code, literal, liability_basis
      FROM annotation.slots_of(%(ids)s::bigint[])
     WHERE NOT unstated
     ORDER BY statement_id, slot_role, alternative
"""

_CONDITIONS = """
    SELECT id, statement_id, condition_role, disjunct_no, concept_code, comparator,
           threshold, measure_unit, negated, needs_assessment,
           value_code, delegate_jurisdiction, delegate_id_scheme, delegate_local_id, delegate_path,
           permit_code, method_code, averaging_code
      FROM annotation.conditions_of(%(ids)s::bigint[])
     ORDER BY statement_id, condition_role, disjunct_no, id
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
    SELECT statement_id, punished_statement_id, legal_effect, amount_text,
           min_amount, max_amount, per_occasion, discretionary
      FROM annotation.sanctions_of(%(ids)s::bigint[])
"""

_TRIGGERS = """
    SELECT statement_id, trigger_statement_id, trigger_kind::text FROM annotation.sanction_trigger
     WHERE statement_id = ANY(%(ids)s)
     ORDER BY statement_id, trigger_statement_id
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
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.term_definition AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id
             WHERE x.id = ANY(%(ids)s)) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.definitions_at(lr.id, %(language)s, %(on)s)
                         OFFSET 0) AS r
      JOIN annotation.term_definition AS t ON t.id = r.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
     WHERE t.id = ANY(%(ids)s)
"""
_GUIDANCE = """
    SELECT d.issuer, d.document_no, d.issued_on, d.rule_kind::text, d.subject,
           coalesce(d.published_in, ''), coalesce(d.source_url, ''), array_agg(g.statement_id)
      FROM guidance.for_statements(%(ids)s, %(decided)s) AS g
      JOIN guidance.document AS d ON d.id = g.document_id
     GROUP BY d.id
     ORDER BY d.issued_on, d.id
"""


class _StatementRow(NamedTuple):
    id: int
    kind: str
    modality: str | None
    version_iri: str


class _InvalidatedRow(NamedTuple):
    statement_id: int
    version_iri: str
    ruling_no: str
    invalid_from: date
    partial: bool


class _SlotRow(NamedTuple):
    statement_id: int
    role: str
    concept: str | None
    literal: str | None
    liability: str | None


class _ConditionRow(NamedTuple):
    id: int
    statement_id: int
    role: str
    disjunct: int
    concept: str
    comparator: str
    threshold: Decimal | None
    unit: str | None
    negated: bool
    needs_assessment: bool
    value_concept: str | None
    delegate_jurisdiction: str | None
    delegate_scheme: str | None
    delegate_local_id: str | None
    delegate_path: str | None
    permit: str | None
    method: str | None
    averaging: str | None


class _ExceptionRow(NamedTuple):
    exception: int
    excluded: int


class _OverrideRow(NamedTuple):
    prevailing: int
    subordinate: int
    basis: str


class _SanctionRow(NamedTuple):
    statement_id: int
    punishes: int
    effect: str
    amount_text: str | None
    min_amount: Decimal | None
    max_amount: Decimal | None
    per_occasion: bool
    discretionary: bool


class _TriggerRow(NamedTuple):
    statement_id: int
    trigger: int
    kind: str


class _SourceRow(NamedTuple):
    statement_id: int
    version_iri: str


class _DefinitionRow(NamedTuple):
    id: int
    version_iri: str
    term: str
    members: list[str]


class _GuidanceRow(NamedTuple):
    issuer: str
    document_no: str
    issued_on: date
    rule_kind: str
    subject: str
    published_in: str
    source_url: str
    statement_ids: list[int]


def definition_sources(
    conn: Conn, definition_ids: Collection[int], on: date
) -> dict[int, tuple[str, str, list[str]]]:
    """定義於 `on` 的出處：（版本 IRI，用語，成員），與推論讀的是同一個 `definitions_at`。"""
    if not definition_ids:
        return {}
    params = {"language": LANGUAGE, "on": on, "ids": sorted(definition_ids)}
    return {
        r.id: (r.version_iri, r.term, r.members)
        for r in fetch(conn, _DEFINITION_SOURCES, params, _DefinitionRow)
    }


def list_statements(conn: Conn, resource_id: int, on: date) -> pb.ListStatementsResponse:
    """資源 `resource_id` 在 `on` 有效、已確認的陳述。"""
    params = {"resource": resource_id, "language": LANGUAGE, "on": on}
    invalidated = [
        outcome_codec.invalidated(
            Invalidated(r.statement_id, r.version_iri, r.ruling_no, r.invalid_from, r.partial)
        )
        for r in fetch(conn, _INVALIDATED, params, _InvalidatedRow)
    ]
    rows = fetch(conn, _STATEMENTS, params, _StatementRow)
    ids = [r.id for r in rows]
    if not ids:
        return pb.ListStatementsResponse(invalidated=invalidated)
    by_id = {"ids": ids}

    statements: dict[int, pb.Statement] = {}
    for row in rows:
        statement = pb.Statement(
            id=row.id, kind=enums.STATEMENT_KIND[row.kind], version_iri=row.version_iri
        )
        if row.modality is not None:
            statement.modality = enums.NORM_MODALITY[row.modality]
        statements[row.id] = statement

    for slot in fetch(conn, _SLOTS, by_id, _SlotRow):
        out = statements[slot.statement_id].slots.add(role=enums.SLOT_ROLE[slot.role])
        if slot.concept is not None:
            out.concept = slot.concept
        else:
            out.literal = str(slot.literal)
        if slot.liability is not None:
            out.liability_basis = enums.LIABILITY_BASIS[slot.liability]

    _conditions(conn, by_id, statements)

    for exc in fetch(conn, _EXCEPTIONS, by_id, _ExceptionRow):
        statements[exc.exception].exception_of.append(exc.excluded)
    for sanc in fetch(conn, _SANCTIONS, by_id, _SanctionRow):
        sanction = statements[sanc.statement_id].sanction
        sanction.punishes = sanc.punishes
        sanction.effect = enums.LEGAL_EFFECT[sanc.effect]
        sanction.amount_text = sanc.amount_text or ""
        if sanc.min_amount is not None:
            sanction.min_amount = int(sanc.min_amount)
        if sanc.max_amount is not None:
            sanction.max_amount = int(sanc.max_amount)
        sanction.per_occasion = sanc.per_occasion
        sanction.discretionary = sanc.discretionary
    # 前提可能在別的法規（環境講習以他法的處分為前提）：識別碼照列，不限於本次列出的陳述
    for trig in fetch(conn, _TRIGGERS, by_id, _TriggerRow):
        statements[trig.statement_id].sanction.triggers.add(
            statement_id=trig.trigger, kind=enums.TRIGGER_KIND[trig.kind]
        )

    return pb.ListStatementsResponse(
        statements=list(statements.values()),
        invalidated=invalidated,
        overrides=[
            pb.Override(
                prevailing=r.prevailing,
                subordinate=r.subordinate,
                basis=enums.OVERRIDE_BASIS[r.basis],
            )
            for r in fetch(conn, _OVERRIDES, by_id, _OverrideRow)
        ],
    )


def _conditions(
    conn: Conn, by_id: dict[str, list[int]], statements: dict[int, pb.Statement]
) -> None:
    """要件依（陳述, 角色）分組，組內依析取編號成為 DNF。"""
    grouped: dict[tuple[int, str], dict[int, list[pb.Condition]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in fetch(conn, _CONDITIONS, by_id, _ConditionRow):
        condition = pb.Condition(
            id=r.id,
            concept=r.concept,
            comparator=enums.COMPARATOR[r.comparator],
            unit=r.unit or "",
            negated=r.negated,
            needs_assessment=r.needs_assessment,
            measurement_method=r.method or "",
            averaging_period=r.averaging or "",
        )
        if r.threshold is not None:
            condition.value.CopyFrom(outcome_codec.decimal(r.threshold))
        elif r.value_concept is not None:
            condition.value_concept = r.value_concept
        elif r.delegate_path is not None:
            condition.delegate.CopyFrom(
                pb.Delegation(
                    identifier=ont_pb.LawIdentifier(
                        jurisdiction=r.delegate_jurisdiction or "",
                        id_scheme=r.delegate_scheme or "",
                        local_id=r.delegate_local_id or "",
                    ),
                    provision_path=r.delegate_path,
                )
            )
        elif r.permit is not None:
            condition.delegate_permit = r.permit
        grouped[(r.statement_id, r.role)][r.disjunct].append(condition)

    for (sid, role), disjuncts in grouped.items():
        statement = statements[sid]
        if role == "limit":
            statement.limits.extend(c for group in disjuncts.values() for c in group)
            continue
        requirement = statement.scope if role == "scope" else statement.duty
        for _, conditions in sorted(disjuncts.items()):
            requirement.any_of.add(all_of=conditions)


def guidance_for(
    conn: Conn, statement_ids: Collection[int], decided: date
) -> list[outcome_codec.GuidanceRef]:
    """處分日 `decided` 可附在這些陳述旁的行政規則（`guidance.for_statements`）。"""
    if not statement_ids:
        return []
    params = {"ids": sorted(statement_ids), "decided": decided}
    return [
        outcome_codec.GuidanceRef(
            r.issuer,
            r.document_no,
            r.issued_on,
            r.rule_kind,
            r.subject,
            r.published_in,
            r.source_url,
            tuple(sorted(r.statement_ids)),
        )
        for r in fetch(conn, _GUIDANCE, params, _GuidanceRow)
    ]


def sources(conn: Conn, statement_ids: Collection[int], on: date) -> dict[int, str]:
    """陳述於 `on` 的出處（版本 IRI）。"""
    if not statement_ids:
        return {}
    params = {"language": LANGUAGE, "on": on, "ids": sorted(statement_ids)}
    return {r.statement_id: r.version_iri for r in fetch(conn, _SOURCES, params, _SourceRow)}
