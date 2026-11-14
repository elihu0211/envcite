"""推論結果轉成 gRPC 回應：陳述出處、授權依據、罰則、減輕與缺口，與處分稽核的結果。"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from envcite.ontology.v2 import ontology_pb2 as ont_pb
from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology.reasoning import Result, scoring
from envcite_ontology.reasoning.engine import Penalty
from envcite_ontology.reasoning.facts.chains import Chain
from envcite_ontology.reasoning.facts.statements import Invalidated
from envcite_ontology.reasoning_codec.enums import (
    CONDITION_ROLE,
    DELEGATION_GAP,
    EXCUSE,
    GUIDANCE_KIND,
    LEGAL_EFFECT,
    LIABILITY_BASIS,
    MITIGATION,
    OVERRIDE_BASIS,
    PROBLEM_KIND,
    REDUCTION_SHARE,
    TRIGGER_KIND,
    VERDICT,
)


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


def decimal(value: Decimal) -> pb.Decimal:
    """不帶指數與多餘尾零的十進位文字。"""
    text = format(value.normalize(), "f")
    return pb.Decimal(value=text if "." in text or value != 0 else "0")


def milli(value: int) -> pb.Decimal:
    """推論程式的千分位整數還原為十進位數。"""
    return decimal(Decimal(value).scaleb(-3))


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
    ids |= {p for p, _ in result.general.justified} | {p for p, _ in result.general.excused}
    ids |= {s for pair in result.mutatis_applied for s in pair}
    return ids


def _delegation(statement_id: int, chain: Chain) -> pb.DelegationBasis:
    return pb.DelegationBasis(
        statement_id=statement_id,
        rooted=chain.rooted,
        links=[
            pb.DelegationLink(
                command_identifier=ont_pb.LawIdentifier(
                    jurisdiction=link.command_jurisdiction,
                    id_scheme=link.command_id_scheme,
                    local_id=link.command_local_id,
                ),
                cited_law_name=link.cited_law_name,
                cited_path=link.cited_path,
                identifier=(
                    ont_pb.LawIdentifier(
                        jurisdiction=link.target_jurisdiction,
                        id_scheme=link.target_id_scheme,
                        local_id=link.target_local_id,
                    )
                    if link.target_jurisdiction and link.target_id_scheme and link.target_local_id
                    else None
                ),
                version_iri=link.version_iri or "",
                gap=DELEGATION_GAP[link.gap],
            )
            for link in chain.links
        ],
    )


def _reduced(row: tuple[int, str, int, int | None, int | None]) -> pb.ReducedFine:
    p, g, k, lo, hi = row
    out = pb.ReducedFine(
        penalty_statement_id=p,
        mitigation=MITIGATION[g],
        share=REDUCTION_SHARE[k],
    )
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


def invalidated(i: Invalidated) -> pb.InvalidatedStatement:
    """違憲失效而不參與推論的陳述，轉成 `InvalidatedStatement` 訊息。"""
    return pb.InvalidatedStatement(
        statement_id=i.statement_id,
        version_iri=i.version_iri,
        ruling_no=i.ruling_no,
        invalid_from=i.invalid_from.isoformat(),
        partial=i.partial,
    )


def outcome(
    result: Result,
    sources: Mapping[int, str],
    uncovered: Iterable[tuple[str, str, str]] = (),
    definitions: Mapping[int, tuple[str, str, list[str]]] | None = None,
    guidance: Iterable[GuidanceRef] = (),
    audit_result: scoring.DispositionAuditResult | None = None,
) -> pb.Outcome:
    """一組推論結果轉成 `Outcome` 訊息。

    陳述的來源版本只列 `sources` 有的；用語定義只列 `definitions` 查得到的。
    """
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
            justified=[
                pb.PenaltyExcuse(penalty_statement_id=p, excuse=EXCUSE[g])
                for p, g in result.general.justified
            ],
            excused=[
                pb.PenaltyExcuse(penalty_statement_id=p, excuse=EXCUSE[g])
                for p, g in result.general.excused
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
        uncovered_law_identifiers=[
            ont_pb.LawIdentifier(
                jurisdiction=jur,
                id_scheme=scheme,
                local_id=local_id,
            )
            for jur, scheme, local_id in uncovered
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
        one_act_groups=[
            pb.ActGroupFine(act_id=act_id, min_amount=lo, max_amount=hi)
            for act_id, (lo, hi) in sorted(result.one_act_groups.items())
        ],
        same_kind_non_fine_overlaps=[
            pb.SameKindNonFineOverlap(act_id=act_id, penalty_1=p1, penalty_2=p2)
            for act_id, p1, p2 in result.same_kind_non_fine_overlaps
        ],
        mutatis_applied=[
            pb.StatementPair(first=src, second=tgt) for src, tgt in result.mutatis_applied
        ],
        scoring_results=[scoring_result(sr) for sr in result.scoring_results],
    )
    if result.one_act is not None:
        out.one_act.CopyFrom(
            pb.FineInterval(min_amount=result.one_act[0], max_amount=result.one_act[1])
        )
    if audit_result is not None:
        out.disposition_audit.CopyFrom(
            pb.DispositionAuditResult(
                d1_citations_complete=audit_result.d1_citations_complete,
                d2_evidence_grounded=audit_result.d2_evidence_grounded,
                d3_defense_and_bounds_valid=audit_result.d3_defense_and_bounds_valid,
                findings=list(audit_result.findings),
            )
        )
    return out


def scoring_result(sr: scoring.ScoringResult) -> pb.ScoringResult:
    """處分的評分結果：矩陣、適用的因子與區間、算式摘要。"""
    out = pb.ScoringResult(
        matrix_code=sr.matrix_code,
        law_title=sr.law_title,
        norm_kind=sr.norm_kind,
        formula_kind=sr.formula_kind,
        equation_summary=sr.equation_summary,
        factors_applied=[
            pb.FactorApplied(
                factor_code=fa.factor_code,
                label_zho=fa.label_zho,
                factor_role=fa.factor_role,
                statutory_ref=fa.statutory_ref,
                value=decimal(fa.value) if fa.value is not None else None,
                interval_min=decimal(fa.interval_min) if fa.interval_min is not None else None,
                interval_max=decimal(fa.interval_max) if fa.interval_max is not None else None,
                missing=fa.missing,
                needs_judgement=fa.needs_judgement,
            )
            for fa in sr.factors_applied
        ],
        raw_min=decimal(sr.raw_min) if sr.raw_min is not None else None,
        raw_max=decimal(sr.raw_max) if sr.raw_max is not None else None,
        lecture_hours_min=sr.lecture_hours_min,
        lecture_hours_max=sr.lecture_hours_max,
        missing_factors=list(sr.missing_factors),
        judgement_factors=list(sr.judgement_factors),
        notes=list(sr.notes),
    )
    if sr.clamped_min is not None:
        out.clamped_min = sr.clamped_min
    if sr.clamped_max is not None:
        out.clamped_max = sr.clamped_max
    if sr.demerit_total is not None:
        out.demerit_total.CopyFrom(decimal(sr.demerit_total))
    if sr.demerit_triggered is not None:
        out.demerit_triggered = sr.demerit_triggered
    return out
