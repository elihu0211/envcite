"""推論結果與案件事實在 gRPC 訊息與領域型別之間的轉換。

列舉一律用明確的對照表：資料庫與推論程式的字串若出現表中沒有的值，是 KeyError（內部錯誤），
不悄悄落到 UNSPECIFIED。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from envcite.ontology.v1 import reasoning_pb2 as pb
from envcite.ontology.v2 import ontology_pb2 as ont_pb2_v2
from envcite.ontology.v2 import reasoning_pb2 as pb2_v2
from envcite_ontology import errors
from envcite_ontology.reasoning import Result, facts
from envcite_ontology.reasoning.engine import Penalty
from envcite_ontology.reasoning.facts import Chain, Measurement

STATEMENT_KIND = {
    "prescriptive": pb.STATEMENT_KIND_PRESCRIPTIVE,
    "constitutive": pb.STATEMENT_KIND_CONSTITUTIVE,
    "sanction": pb.STATEMENT_KIND_PENALTY,
    "reparation": pb.STATEMENT_KIND_REPARATION,
}
NORM_MODALITY = {
    "obligation": pb.NORM_MODALITY_OBLIGATION,
    "prohibition": pb.NORM_MODALITY_PROHIBITION,
    "permission": pb.NORM_MODALITY_PERMISSION,
}
SLOT_ROLE = {
    "subject": pb.SLOT_ROLE_SUBJECT,
    "action": pb.SLOT_ROLE_ACTION,
    "object": pb.SLOT_ROLE_OBJECT,
    "medium": pb.SLOT_ROLE_MEDIUM,
    "time": pb.SLOT_ROLE_TIME,
    "place": pb.SLOT_ROLE_PLACE,
}
COMPARATOR = {
    "le": pb.COMPARATOR_LE,
    "lt": pb.COMPARATOR_LT,
    "ge": pb.COMPARATOR_GE,
    "gt": pb.COMPARATOR_GT,
    "eq": pb.COMPARATOR_EQ,
    "ne": pb.COMPARATOR_NE,
}
CONDITION_ROLE = {
    "scope": pb.CONDITION_ROLE_SCOPE,
    "duty": pb.CONDITION_ROLE_DUTY,
    "limit": pb.CONDITION_ROLE_LIMIT,
}
LEGAL_EFFECT = {
    "fine": pb.LEGAL_EFFECT_FINE,
    "confiscation": pb.LEGAL_EFFECT_CONFISCATION,
    "restriction": pb.LEGAL_EFFECT_RESTRICTION,
    "deprivation": pb.LEGAL_EFFECT_DEPRIVATION,
    "reputation": pb.LEGAL_EFFECT_REPUTATION,
    "warning": pb.LEGAL_EFFECT_WARNING,
    "order": pb.LEGAL_EFFECT_ORDER,
    "disgorgement": pb.LEGAL_EFFECT_DISGORGEMENT,
}
LIABILITY_BASIS = {
    "act": pb.LIABILITY_BASIS_ACT,
    "state": pb.LIABILITY_BASIS_STATE,
    "representative": pb.LIABILITY_BASIS_REPRESENTATIVE,
}
TRIGGER_KIND = {
    "order_lapsed": pb.TRIGGER_KIND_ORDER_LAPSED,
    "sanction_imposed": pb.TRIGGER_KIND_SANCTION_IMPOSED,
}


@dataclass(frozen=True)
class GuidanceRef:
    """附在結果旁的一則行政規則（`annotation_read.guidance_for`）。"""

    issuer: str
    document_no: str
    issued_on: date
    kind: str
    subject: str
    published_in: str
    source_url: str
    statement_ids: tuple[int, ...]


GUIDANCE_KIND = {
    "interpretation": pb.GUIDANCE_KIND_INTERPRETATION,
    "discretion_standard": pb.GUIDANCE_KIND_DISCRETION_STANDARD,
    "operational": pb.GUIDANCE_KIND_OPERATIONAL,
}
OVERRIDE_BASIS = {
    "hierarchy": pb.OVERRIDE_BASIS_HIERARCHY,
    "lex_specialis": pb.OVERRIDE_BASIS_LEX_SPECIALIS,
    "lex_posterior": pb.OVERRIDE_BASIS_LEX_POSTERIOR,
    "exception": pb.OVERRIDE_BASIS_EXCEPTION,
}
VERDICT = {
    "violation": pb.VERDICT_VIOLATION,
    "compliant": pb.VERDICT_COMPLIANT,
    "conditional_violation": pb.VERDICT_CONDITIONAL_VIOLATION,
    "conditional_compliant": pb.VERDICT_CONDITIONAL_COMPLIANT,
    "undetermined": pb.VERDICT_UNDETERMINED,
    "excluded": pb.VERDICT_EXCLUDED,
    "not_applicable": pb.VERDICT_NOT_APPLICABLE,
}
PROBLEM_KIND = {
    "contradictory_fact": pb.PROBLEM_KIND_CONTRADICTORY_FACT,
    "unresolved_delegation": pb.PROBLEM_KIND_UNRESOLVED_DELEGATION,
    "bad_date": pb.PROBLEM_KIND_BAD_DATE,
    "measurement_mismatch": pb.PROBLEM_KIND_MEASUREMENT_MISMATCH,
    "measurement_unlabeled": pb.PROBLEM_KIND_MEASUREMENT_UNLABELED,
    "invalidated_delegation": pb.PROBLEM_KIND_INVALIDATED_DELEGATION,
}
EXCUSE = {
    "no_fault": pb.EXCUSE_NO_FAULT,
    "under_fourteen": pb.EXCUSE_UNDER_FOURTEEN,
    "incapable": pb.EXCUSE_INCAPABLE,
    "pursuant_to_law": pb.EXCUSE_PURSUANT_TO_LAW,
    "superior_order": pb.EXCUSE_SUPERIOR_ORDER,
    "self_defense": pb.EXCUSE_SELF_DEFENSE,
    "necessity": pb.EXCUSE_NECESSITY,
    "criminal_priority": pb.EXCUSE_CRIMINAL_PRIORITY,
}
MITIGATION = {
    "youth": pb.MITIGATION_YOUTH,
    "reduced_capacity": pb.MITIGATION_REDUCED_CAPACITY,
    "ignorance": pb.MITIGATION_IGNORANCE,
    "excessive_defense": pb.MITIGATION_EXCESSIVE_DEFENSE,
    "excessive_necessity": pb.MITIGATION_EXCESSIVE_NECESSITY,
}
REDUCTION_SHARE = {2: pb.REDUCTION_SHARE_HALF, 3: pb.REDUCTION_SHARE_THIRD}
DELEGATION_GAP = {
    None: pb.DELEGATION_GAP_UNSPECIFIED,
    "no_citation": pb.DELEGATION_GAP_NO_CITATION,
    "unresolved_law": pb.DELEGATION_GAP_UNRESOLVED_LAW,
    "law_not_in_force": pb.DELEGATION_GAP_LAW_NOT_IN_FORCE,
    "law_invalidated": pb.DELEGATION_GAP_LAW_INVALIDATED,
    "cycle": pb.DELEGATION_GAP_CYCLE,
}

STATEMENT_KIND_V2 = {
    "prescriptive": pb2_v2.STATEMENT_KIND_PRESCRIPTIVE,
    "constitutive": pb2_v2.STATEMENT_KIND_CONSTITUTIVE,
    "sanction": pb2_v2.STATEMENT_KIND_PENALTY,
    "reparation": pb2_v2.STATEMENT_KIND_REPARATION,
}
NORM_MODALITY_V2 = {
    "obligation": pb2_v2.NORM_MODALITY_OBLIGATION,
    "prohibition": pb2_v2.NORM_MODALITY_PROHIBITION,
    "permission": pb2_v2.NORM_MODALITY_PERMISSION,
}
SLOT_ROLE_V2 = {
    "subject": pb2_v2.SLOT_ROLE_SUBJECT,
    "action": pb2_v2.SLOT_ROLE_ACTION,
    "object": pb2_v2.SLOT_ROLE_OBJECT,
    "medium": pb2_v2.SLOT_ROLE_MEDIUM,
    "time": pb2_v2.SLOT_ROLE_TIME,
    "place": pb2_v2.SLOT_ROLE_PLACE,
}
COMPARATOR_V2 = {
    "le": pb2_v2.COMPARATOR_LE,
    "lt": pb2_v2.COMPARATOR_LT,
    "ge": pb2_v2.COMPARATOR_GE,
    "gt": pb2_v2.COMPARATOR_GT,
    "eq": pb2_v2.COMPARATOR_EQ,
    "ne": pb2_v2.COMPARATOR_NE,
}
CONDITION_ROLE_V2 = {
    "scope": pb2_v2.CONDITION_ROLE_SCOPE,
    "duty": pb2_v2.CONDITION_ROLE_DUTY,
    "limit": pb2_v2.CONDITION_ROLE_LIMIT,
}
LEGAL_EFFECT_V2 = {
    "fine": pb2_v2.LEGAL_EFFECT_FINE,
    "confiscation": pb2_v2.LEGAL_EFFECT_CONFISCATION,
    "restriction": pb2_v2.LEGAL_EFFECT_RESTRICTION,
    "deprivation": pb2_v2.LEGAL_EFFECT_DEPRIVATION,
    "reputation": pb2_v2.LEGAL_EFFECT_REPUTATION,
    "warning": pb2_v2.LEGAL_EFFECT_WARNING,
    "order": pb2_v2.LEGAL_EFFECT_ORDER,
    "disgorgement": pb2_v2.LEGAL_EFFECT_DISGORGEMENT,
}
LIABILITY_BASIS_V2 = {
    "act": pb2_v2.LIABILITY_BASIS_ACT,
    "state": pb2_v2.LIABILITY_BASIS_STATE,
    "representative": pb2_v2.LIABILITY_BASIS_REPRESENTATIVE,
}
TRIGGER_KIND_V2 = {
    "order_lapsed": pb2_v2.TRIGGER_KIND_ORDER_LAPSED,
    "sanction_imposed": pb2_v2.TRIGGER_KIND_SANCTION_IMPOSED,
}
GUIDANCE_KIND_V2 = {
    "interpretation": pb2_v2.GUIDANCE_KIND_INTERPRETATION,
    "discretion_standard": pb2_v2.GUIDANCE_KIND_DISCRETION_STANDARD,
    "operational": pb2_v2.GUIDANCE_KIND_OPERATIONAL,
}
OVERRIDE_BASIS_V2 = {
    "hierarchy": pb2_v2.OVERRIDE_BASIS_HIERARCHY,
    "lex_specialis": pb2_v2.OVERRIDE_BASIS_LEX_SPECIALIS,
    "lex_posterior": pb2_v2.OVERRIDE_BASIS_LEX_POSTERIOR,
    "exception": pb2_v2.OVERRIDE_BASIS_EXCEPTION,
}
VERDICT_V2 = {
    "violation": pb2_v2.VERDICT_VIOLATION,
    "compliant": pb2_v2.VERDICT_COMPLIANT,
}
EXCUSE_V2 = {
    "no_fault": pb2_v2.EXCUSE_NO_FAULT,
    "under_fourteen": pb2_v2.EXCUSE_UNDER_FOURTEEN,
    "incapable": pb2_v2.EXCUSE_INCAPABLE,
    "pursuant_to_law": pb2_v2.EXCUSE_PURSUANT_TO_LAW,
    "superior_order": pb2_v2.EXCUSE_SUPERIOR_ORDER,
    "self_defense": pb2_v2.EXCUSE_SELF_DEFENSE,
    "necessity": pb2_v2.EXCUSE_NECESSITY,
    "criminal_priority": pb2_v2.EXCUSE_CRIMINAL_PRIORITY,
}
MITIGATION_V2 = {
    "youth": pb2_v2.MITIGATION_YOUTH,
    "reduced_capacity": pb2_v2.MITIGATION_REDUCED_CAPACITY,
    "ignorance": pb2_v2.MITIGATION_IGNORANCE,
    "excessive_defense": pb2_v2.MITIGATION_EXCESSIVE_DEFENSE,
    "excessive_necessity": pb2_v2.MITIGATION_EXCESSIVE_NECESSITY,
}
REDUCTION_SHARE_V2 = {2: pb2_v2.REDUCTION_SHARE_HALF, 3: pb2_v2.REDUCTION_SHARE_THIRD}
DELEGATION_GAP_V2 = {
    None: pb2_v2.DELEGATION_GAP_UNSPECIFIED,
    "no_citation": pb2_v2.DELEGATION_GAP_NO_CITATION,
    "unresolved_law": pb2_v2.DELEGATION_GAP_UNRESOLVED_LAW,
    "law_not_in_force": pb2_v2.DELEGATION_GAP_LAW_NOT_IN_FORCE,
    "law_invalidated": pb2_v2.DELEGATION_GAP_LAW_INVALIDATED,
    "cycle": pb2_v2.DELEGATION_GAP_CYCLE,
}
PROBLEM_KIND_V2 = {
    "contradictory_fact": pb2_v2.PROBLEM_KIND_CONTRADICTORY_FACT,
    "unresolved_delegation": pb2_v2.PROBLEM_KIND_UNRESOLVED_DELEGATION,
    "bad_date": pb2_v2.PROBLEM_KIND_BAD_DATE,
    "measurement_mismatch": pb2_v2.PROBLEM_KIND_MEASUREMENT_MISMATCH,
    "measurement_unlabeled": pb2_v2.PROBLEM_KIND_MEASUREMENT_UNLABELED,
    "invalidated_delegation": pb2_v2.PROBLEM_KIND_INVALIDATED_DELEGATION,
}

#: 推論程式為行政罰法總則保留的案件事實代碼前綴。
RESERVED_PREFIX = "ap_"

_BooleanFact = Literal[
    "intent",
    "negligence",
    "agent_fault",
    "incapable",
    "self_induced",
    "pursuant_to_law",
    "superior_order",
    "knew_illegal",
    "stated_opinion",
    "self_defense",
    "defense_excessive",
    "necessity",
    "necessity_excessive",
    "capacity_reduced",
    "ignorance_of_law",
    "criminal_offense",
    "court_confiscated",
]
_BOOLEAN_FACTS: tuple[_BooleanFact, ...] = (
    "intent",
    "negligence",
    "agent_fault",
    "incapable",
    "self_induced",
    "pursuant_to_law",
    "superior_order",
    "knew_illegal",
    "stated_opinion",
    "self_defense",
    "defense_excessive",
    "necessity",
    "necessity_excessive",
    "capacity_reduced",
    "ignorance_of_law",
    "criminal_offense",
    "court_confiscated",
)
_DecimalFact = Literal[
    "criminal_payment", "community_service_hours", "minimum_hourly_wage", "gained_benefit"
]
_DECIMAL_FACTS: tuple[_DecimalFact, ...] = (
    "criminal_payment",
    "community_service_hours",
    "minimum_hourly_wage",
    "gained_benefit",
)


def decimal(value: Decimal) -> pb.Decimal:
    """不帶指數與多餘尾零的十進位文字。"""
    text = format(value.normalize(), "f")
    return pb.Decimal(value=text if "." in text or value != 0 else "0")


def milli(value: int) -> pb.Decimal:
    """推論程式的千分位整數還原為十進位數。"""
    return decimal(Decimal(value).scaleb(-3))


def decimal_v2(value: Decimal) -> pb2_v2.Decimal:
    """不帶指數與多餘尾零的十進位文字（v2）。"""
    text = format(value.normalize(), "f")
    return pb2_v2.Decimal(value=text if "." in text or value != 0 else "0")


def milli_v2(value: int) -> pb2_v2.Decimal:
    """推論程式的千分位整數還原為十進位數（v2）。"""
    return decimal_v2(Decimal(value).scaleb(-3))


def statement_ids(result: Result) -> set[int]:
    """結果裡提到的每一個陳述編號，用來查出處。"""
    ids: set[int] = set(result.verdicts)
    ids |= {p.statement_id for p in result.penalties} | {p.punishes_id for p in result.penalties}
    ids |= {q for p in result.penalties for q, _ in p.triggered_by}
    ids |= {s for s, _, _ in result.missing_permits} | set(result.subject_mismatch)
    for gaps in (result.missing, result.needs_judgement, result.judgement_used):
        ids |= {s for s, _ in gaps}
    for pairs in (result.unresolved_conflicts, result.unresolved_penalty_overlaps):
        ids |= {i for pair in pairs for i in pair}
    ids |= {i for d in result.defeated for i in d[:2]}
    ids |= {i for d in result.penalty_defeated for i in d[:2]}
    ids |= {t.statement_id for t in result.trace} | set(result.limit_sources.values())
    ids |= {p for p, _ in result.general.unpunished} | {p for p, _ in result.general.mitigation}
    ids |= {p for p, _ in result.general.presumed} | set(result.general.time_barred)
    ids |= {r[0] for r in result.general.reduced} | {p for p, _ in result.general.raised_caps}
    ids |= {p for p, _ in result.general.deductions}
    return ids


def _delegation(statement_id: int, chain: Chain) -> pb.DelegationBasis:
    return pb.DelegationBasis(
        statement_id=statement_id,
        rooted=chain.rooted,
        links=[
            pb.DelegationLink(
                command_law_id=link.command_local_id,
                cited_law_name=link.cited_law_name,
                cited_path=link.cited_path,
                law_id=link.target_local_id or "",
                version_iri=link.version_iri or "",
                gap=DELEGATION_GAP[link.gap],
            )
            for link in chain.links
        ],
    )


def _reduced(row: tuple[int, str, int, int | None, int | None]) -> pb.ReducedFine:
    p, g, k, lo, hi = row
    out = pb.ReducedFine(penalty_statement_id=p, mitigation=MITIGATION[g], share=REDUCTION_SHARE[k])
    if lo is not None:
        out.min_amount = lo
    if hi is not None:
        out.max_amount = hi
    return out


def _penalty(p: Penalty) -> pb.PenaltyCandidate:
    out = pb.PenaltyCandidate(
        statement_id=p.statement_id,
        punishes=p.punishes_id,
        effect=LEGAL_EFFECT[p.effect],
        conditional=p.conditional,
        per_occasion=p.repeating,
        discretionary=p.discretionary,
        subject_bases=[LIABILITY_BASIS[b] for b in p.subject_bases],
        triggered_by=[
            pb.SanctionTrigger(statement_id=q, kind=TRIGGER_KIND[k]) for q, k in p.triggered_by
        ],
    )
    if p.min_amount is not None:
        out.min_amount = p.min_amount
    if p.max_amount is not None:
        out.max_amount = p.max_amount
    return out


def _gaps(rows: Iterable[tuple[int, str]]) -> list[pb.Gap]:
    return [pb.Gap(statement_id=s, concept=c) for s, c in rows]


def _problem(kind: str, subject: int | str) -> pb.Problem:
    out = pb.Problem(kind=PROBLEM_KIND[kind])
    if isinstance(subject, int):
        out.condition_id = subject
    else:
        out.concept = subject
    return out


def outcome(
    result: Result,
    sources: Mapping[int, str],
    uncovered: Iterable[str | tuple[str, str, str]] = (),
    definitions: Mapping[int, tuple[str, str, list[str]]] | None = None,
    guidance: Iterable[GuidanceRef] = (),
) -> pb.Outcome:
    out = pb.Outcome(
        verdicts=[
            pb.StatementVerdict(statement_id=s, verdict=VERDICT[v])
            for s, v in sorted(result.verdicts.items())
        ],
        penalties=[_penalty(p) for p in result.penalties],
        missing=_gaps(result.missing),
        needs_assessment=_gaps(result.needs_judgement),
        assessment_used=_gaps(result.judgement_used),
        problems=[_problem(k, v) for k, v in result.problems],
        unresolved_conflicts=[
            pb.StatementPair(first=a, second=b) for a, b in result.unresolved_conflicts
        ],
        defeated=[
            pb.Defeat(loser=lo, winner=w, basis=OVERRIDE_BASIS[b]) for lo, w, b in result.defeated
        ],
        unresolved_penalty_overlaps=[
            pb.StatementPair(first=a, second=b) for a, b in result.unresolved_penalty_overlaps
        ],
        penalty_defeated=[
            pb.Defeat(loser=lo, winner=w, basis=OVERRIDE_BASIS[b])
            for lo, w, b in result.penalty_defeated
        ],
        trace=[
            pb.TraceEntry(
                condition_id=t.condition_id,
                statement_id=t.statement_id,
                concept=t.concept,
                fact=milli(t.fact),
                threshold=milli(t.threshold),
                role=CONDITION_ROLE[t.role],
            )
            for t in result.trace
        ],
        limit_sources=dict(result.limit_sources),
        general=pb.GeneralPart(
            unpunished=[
                pb.PenaltyExcuse(penalty_statement_id=p, excuse=EXCUSE[g])
                for p, g in result.general.unpunished
            ],
            mitigation=[
                pb.PenaltyMitigation(penalty_statement_id=p, mitigation=MITIGATION[g])
                for p, g in result.general.mitigation
            ],
            presumed_agent_fault=[p for p, _ in result.general.presumed],
            time_barred=list(result.general.time_barred),
            reduced=[_reduced(r) for r in result.general.reduced],
            raised_caps=[
                pb.RaisedCap(penalty_statement_id=p, max_amount=decimal(v))
                for p, v in result.general.raised_caps
            ],
            criminal_deductions=[
                pb.CriminalDeduction(penalty_statement_id=p, amount=decimal(v))
                for p, v in result.general.deductions
            ],
        ),
        sources=[
            pb.StatementSource(statement_id=i, version_iri=sources[i])
            for i in sorted(statement_ids(result))
            if i in sources
        ],
        subject_mismatch=list(result.subject_mismatch),
        invalidated=[invalidated(i) for i in result.invalidated],
        delegation_bases=[
            _delegation(i, result.delegation[i])
            for i in sorted(statement_ids(result))
            if i in result.delegation
        ],
        uncovered_law_ids=[
            x[2] if isinstance(x, tuple) and len(x) == 3 else str(x) for x in uncovered
        ],
        missing_permit_limits=[
            pb.PermitGap(statement_id=s, permit=k, concept=m) for s, k, m in result.missing_permits
        ],
        definitions_used=[
            pb.DefinitionUse(
                condition_id=c,
                definition_id=d,
                version_iri=known[0],
                term=known[1],
                members=known[2],
            )
            for c, d in sorted(result.definitions_used)
            if (known := (definitions or {}).get(d)) is not None
        ],
        guidance=[
            pb.Guidance(
                issuer=g.issuer,
                document_no=g.document_no,
                issued_on=g.issued_on.isoformat(),
                kind=GUIDANCE_KIND[g.kind],
                subject=g.subject,
                statement_ids=list(g.statement_ids),
                published_in=g.published_in,
                source_url=g.source_url,
            )
            for g in guidance
        ],
    )
    if result.one_act is not None:
        out.one_act.CopyFrom(
            pb.FineInterval(min_amount=result.one_act[0], max_amount=result.one_act[1])
        )
    return out


def _delegation_v2(statement_id: int, chain: Chain) -> pb2_v2.DelegationBasis:
    return pb2_v2.DelegationBasis(
        statement_id=statement_id,
        rooted=chain.rooted,
        links=[
            pb2_v2.DelegationLink(
                command_identifier=ont_pb2_v2.LawIdentifier(
                    jurisdiction=link.command_jurisdiction,
                    id_scheme=link.command_id_scheme,
                    local_id=link.command_local_id,
                ),
                cited_law_name=link.cited_law_name,
                cited_path=link.cited_path,
                identifier=(
                    ont_pb2_v2.LawIdentifier(
                        jurisdiction=link.target_jurisdiction,
                        id_scheme=link.target_id_scheme,
                        local_id=link.target_local_id,
                    )
                    if link.target_jurisdiction and link.target_id_scheme and link.target_local_id
                    else None
                ),
                version_iri=link.version_iri or "",
                gap=DELEGATION_GAP_V2[link.gap],
            )
            for link in chain.links
        ],
    )


def _reduced_v2(row: tuple[int, str, int, int | None, int | None]) -> pb2_v2.ReducedFine:
    p, g, k, lo, hi = row
    out = pb2_v2.ReducedFine(
        penalty_statement_id=p,
        mitigation=MITIGATION_V2[g],
        share=REDUCTION_SHARE_V2[k],
    )
    if lo is not None:
        out.min_amount = lo
    if hi is not None:
        out.max_amount = hi
    return out


def _penalty_v2(p: Penalty) -> pb2_v2.PenaltyCandidate:
    out = pb2_v2.PenaltyCandidate(
        statement_id=p.statement_id,
        punishes=p.punishes_id,
        effect=LEGAL_EFFECT_V2[p.effect],
        conditional=p.conditional,
        per_occasion=p.repeating,
        discretionary=p.discretionary,
        subject_bases=[LIABILITY_BASIS_V2[b] for b in p.subject_bases],
        triggered_by=[
            pb2_v2.SanctionTrigger(statement_id=q, kind=TRIGGER_KIND_V2[k])
            for q, k in p.triggered_by
        ],
    )
    if p.min_amount is not None:
        out.min_amount = p.min_amount
    if p.max_amount is not None:
        out.max_amount = p.max_amount
    return out


def _gaps_v2(rows: Iterable[tuple[int, str]]) -> list[pb2_v2.Gap]:
    return [pb2_v2.Gap(statement_id=s, concept=c) for s, c in rows]


def _problem_v2(kind: str, subject: int | str) -> pb2_v2.Problem:
    out = pb2_v2.Problem(kind=PROBLEM_KIND_V2[kind])
    if isinstance(subject, int):
        out.condition_id = subject
    else:
        out.concept = subject
    return out


def invalidated_v2(i: facts.Invalidated) -> pb2_v2.InvalidatedStatement:
    return pb2_v2.InvalidatedStatement(
        statement_id=i.statement_id,
        version_iri=i.version_iri,
        ruling_no=i.ruling_no,
        invalid_from=i.invalid_from.isoformat(),
        partial=i.partial,
    )


def outcome_v2(
    result: Result,
    sources: Mapping[int, str],
    uncovered: Iterable[tuple[str, str, str]] = (),
    definitions: Mapping[int, tuple[str, str, list[str]]] | None = None,
    guidance: Iterable[GuidanceRef] = (),
) -> pb2_v2.Outcome:
    out = pb2_v2.Outcome(
        verdicts=[
            pb2_v2.StatementVerdict(statement_id=s, verdict=VERDICT_V2[v])
            for s, v in sorted(result.verdicts.items())
        ],
        penalties=[_penalty_v2(p) for p in result.penalties],
        missing=_gaps_v2(result.missing),
        needs_assessment=_gaps_v2(result.needs_judgement),
        assessment_used=_gaps_v2(result.judgement_used),
        problems=[_problem_v2(k, v) for k, v in result.problems],
        unresolved_conflicts=[
            pb2_v2.StatementPair(first=a, second=b) for a, b in result.unresolved_conflicts
        ],
        defeated=[
            pb2_v2.Defeat(loser=lo, winner=w, basis=OVERRIDE_BASIS_V2[b])
            for lo, w, b in result.defeated
        ],
        unresolved_penalty_overlaps=[
            pb2_v2.StatementPair(first=a, second=b) for a, b in result.unresolved_penalty_overlaps
        ],
        penalty_defeated=[
            pb2_v2.Defeat(loser=lo, winner=w, basis=OVERRIDE_BASIS_V2[b])
            for lo, w, b in result.penalty_defeated
        ],
        trace=[
            pb2_v2.TraceEntry(
                condition_id=t.condition_id,
                statement_id=t.statement_id,
                concept=t.concept,
                fact=milli_v2(t.fact),
                threshold=milli_v2(t.threshold),
                role=CONDITION_ROLE_V2[t.role],
            )
            for t in result.trace
        ],
        limit_sources=dict(result.limit_sources),
        general=pb2_v2.GeneralPart(
            unpunished=[
                pb2_v2.PenaltyExcuse(penalty_statement_id=p, excuse=EXCUSE_V2[g])
                for p, g in result.general.unpunished
            ],
            mitigation=[
                pb2_v2.PenaltyMitigation(penalty_statement_id=p, mitigation=MITIGATION_V2[g])
                for p, g in result.general.mitigation
            ],
            presumed_agent_fault=[p for p, _ in result.general.presumed],
            time_barred=list(result.general.time_barred),
            reduced=[_reduced_v2(r) for r in result.general.reduced],
            raised_caps=[
                pb2_v2.RaisedCap(penalty_statement_id=p, max_amount=decimal_v2(v))
                for p, v in result.general.raised_caps
            ],
            criminal_deductions=[
                pb2_v2.CriminalDeduction(penalty_statement_id=p, amount=decimal_v2(v))
                for p, v in result.general.deductions
            ],
        ),
        sources=[
            pb2_v2.StatementSource(statement_id=i, version_iri=sources[i])
            for i in sorted(statement_ids(result))
            if i in sources
        ],
        subject_mismatch=list(result.subject_mismatch),
        invalidated=[invalidated_v2(i) for i in result.invalidated],
        delegation_bases=[
            _delegation_v2(i, result.delegation[i])
            for i in sorted(statement_ids(result))
            if i in result.delegation
        ],
        uncovered_law_identifiers=[
            ont_pb2_v2.LawIdentifier(
                jurisdiction=jur,
                id_scheme=scheme,
                local_id=local_id,
            )
            for jur, scheme, local_id in uncovered
        ],
        missing_permit_limits=[
            pb2_v2.PermitGap(statement_id=s, permit=k, concept=m)
            for s, k, m in result.missing_permits
        ],
        definitions_used=[
            pb2_v2.DefinitionUse(
                condition_id=c,
                definition_id=d,
                version_iri=known[0],
                term=known[1],
                members=known[2],
            )
            for c, d in sorted(result.definitions_used)
            if (known := (definitions or {}).get(d)) is not None
        ],
        guidance=[
            pb2_v2.Guidance(
                issuer=g.issuer,
                document_no=g.document_no,
                issued_on=g.issued_on.isoformat(),
                kind=GUIDANCE_KIND_V2[g.kind],
                subject=g.subject,
                statement_ids=list(g.statement_ids),
                published_in=g.published_in,
                source_url=g.source_url,
            )
            for g in guidance
        ],
    )
    if result.one_act is not None:
        out.one_act.CopyFrom(
            pb2_v2.FineInterval(min_amount=result.one_act[0], max_amount=result.one_act[1])
        )
    return out


def invalidated(i: facts.Invalidated) -> pb.InvalidatedStatement:
    return pb.InvalidatedStatement(
        statement_id=i.statement_id,
        version_iri=i.version_iri,
        ruling_no=i.ruling_no,
        invalid_from=i.invalid_from.isoformat(),
        partial=i.partial,
    )


def _stamp(day: date) -> float:
    return float(day.year * 10000 + day.month * 100 + day.day)


def _number(value: Any) -> Decimal | None:
    """有限的十進位數；不是則 None。"""
    try:
        number = Decimal(value.value)
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def measurements(
    request: Any,
) -> dict[str, list[Measurement]]:
    """事實的認定方式；兩者都沒給的事實不列（未提供）。"""
    out: dict[str, list[Measurement]] = {}
    for fact in request.facts:
        if fact.concept and (fact.measurement_method or fact.averaging_period):
            out.setdefault(fact.concept, []).append(
                Measurement(fact.measurement_method or None, fact.averaging_period or None)
            )
    return out


def permit_limits(request: Any, validator: errors.Validator) -> dict[tuple[str, str], list[float]]:
    """許可所載的量值，（許可種類，概念）→ 值。欄位錯誤記在 `validator`。"""
    out: dict[tuple[str, str], list[float]] = {}
    for i, limit in enumerate(request.permit_limits):
        where = f"permit_limits[{i}]"
        if not limit.permit:
            validator.add(f"{where}.permit", errors.EMPTY, "permit is required")
        if not limit.concept:
            validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
        number = _number(limit.value)
        if number is None:
            validator.add(
                f"{where}.value.value", errors.INVALID_NUMBER, "must be a finite decimal number"
            )
        if limit.permit and limit.concept and number is not None:
            out.setdefault((limit.permit, limit.concept), []).append(float(number))
    return out


def actor_bases(request: Any, validator: errors.Validator) -> list[str]:
    """行為人所屬的責任基礎，推論程式用的名稱。欄位錯誤記在 `validator`。"""
    names: dict[int, str] = {int(value): name for name, value in LIABILITY_BASIS.items()}
    out: list[str] = []
    for i, basis in enumerate(request.actor_bases):
        b_val = int(basis)
        if b_val not in names:
            validator.add(f"actor_bases[{i}]", errors.UNSET, "a liability basis is required")
        else:
            out.append(names[b_val])
    return out


def case_facts(
    request: Any,
    validator: errors.Validator,
    conduct_ended: date | None,
    result_on: date | None,
    criminal_cleared: date | None,
) -> dict[str, float | list[float | str]]:
    """請求的案件事實轉成推論程式的輸入。欄位錯誤記在 `validator`，不丟例外。"""
    several: dict[str, list[float | str]] = {}
    for i, fact in enumerate(request.facts):
        where = f"facts[{i}]"
        if not fact.concept:
            validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
            continue
        if fact.concept.startswith(RESERVED_PREFIX):
            validator.add(
                f"{where}.concept",
                errors.RESERVED_CONCEPT,
                f"codes beginning {RESERVED_PREFIX!r} are reserved;"
                " use administrative_penalty_facts",
            )
            continue
        if not fact.values:
            validator.add(f"{where}.values", errors.EMPTY, "at least one value is required")
            continue
        values: list[float | str] = []
        for j, v in enumerate(fact.values):
            kind = v.WhichOneof("value")
            if kind == "flag":
                values.append(1.0 if v.flag else 0.0)
            elif kind == "concept":
                if not v.concept:
                    validator.add(
                        f"{where}.values[{j}].concept", errors.EMPTY, "concept is required"
                    )
                    continue
                values.append(v.concept)
            elif kind == "number":
                number = _number(v.number)
                if number is None:
                    validator.add(
                        f"{where}.values[{j}].number.value",
                        errors.INVALID_NUMBER,
                        "must be a finite decimal number",
                    )
                    continue
                values.append(float(number))
            else:
                validator.add(
                    f"{where}.values[{j}]", errors.UNSET, "flag, number or concept is required"
                )
        if values:
            several.setdefault(fact.concept, []).extend(values)
    case: dict[str, float | list[float | str]] = dict(several)
    penalty = request.administrative_penalty_facts
    for name in _BOOLEAN_FACTS:
        if penalty.HasField(name):
            case[f"{RESERVED_PREFIX}{name}"] = 1.0 if getattr(penalty, name) else 0.0
    if penalty.HasField("age"):
        if penalty.age < 0:
            validator.add(
                "administrative_penalty_facts.age", errors.OUT_OF_RANGE, "age must not be negative"
            )
        else:
            case[f"{RESERVED_PREFIX}age"] = float(penalty.age)
    for decimal_name in _DECIMAL_FACTS:
        if penalty.HasField(decimal_name):
            where = f"administrative_penalty_facts.{decimal_name}"
            number = _number(getattr(penalty, decimal_name))
            if number is None:
                validator.add(
                    f"{where}.value", errors.INVALID_NUMBER, "must be a finite decimal number"
                )
            elif number < 0:
                validator.add(where, errors.OUT_OF_RANGE, "must not be negative")
            else:
                case[f"{RESERVED_PREFIX}{decimal_name}"] = float(number)
    if criminal_cleared is not None:
        case[f"{RESERVED_PREFIX}criminal_cleared"] = _stamp(criminal_cleared)
    if conduct_ended is not None:
        case[f"{RESERVED_PREFIX}conduct_ended"] = _stamp(conduct_ended)
    if result_on is not None:
        case[f"{RESERVED_PREFIX}result_on"] = _stamp(result_on)
    return case
