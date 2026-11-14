"""推論結果與案件事實在 gRPC 訊息與領域型別之間的轉換。

列舉一律用明確的對照表：資料庫與推論程式的字串若出現表中沒有的值，是 KeyError（內部錯誤），
不悄悄落到 UNSPECIFIED。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from envcite.ontology.v2 import ontology_pb2 as ont_pb
from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology import errors
from envcite_ontology.reasoning import Result, facts, scoring
from envcite_ontology.reasoning.engine import Penalty
from envcite_ontology.reasoning.facts import Chain, Measurement, PerOccasionTimeline


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
    "mutatis_mutandis": pb.OVERRIDE_BASIS_MUTATIS_MUTANDIS,
    "subsidiarity": pb.OVERRIDE_BASIS_SUBSIDIARITY,
    "absorption": pb.OVERRIDE_BASIS_ABSORPTION,
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
EXCUSE = {
    "no_fault": pb.EXCUSE_NO_FAULT,
    "under_fourteen": pb.EXCUSE_UNDER_FOURTEEN,
    "incapable": pb.EXCUSE_INCAPABLE,
    "pursuant_to_law": pb.EXCUSE_PURSUANT_TO_LAW,
    "superior_order": pb.EXCUSE_SUPERIOR_ORDER,
    "self_defense": pb.EXCUSE_SELF_DEFENSE,
    "necessity": pb.EXCUSE_NECESSITY,
    "criminal_priority": pb.EXCUSE_CRIMINAL_PRIORITY,
    "unavoidable_ignorance": pb.EXCUSE_UNAVOIDABLE_IGNORANCE,
    "unexpectable": pb.EXCUSE_UNEXPECTABLE,
    "petty_offense": pb.EXCUSE_PETTY_OFFENSE,
    "within_cure_period": pb.EXCUSE_WITHIN_CURE_PERIOD,
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
PROBLEM_KIND = {
    "contradictory_fact": pb.PROBLEM_KIND_CONTRADICTORY_FACT,
    "unresolved_delegation": pb.PROBLEM_KIND_UNRESOLVED_DELEGATION,
    "bad_date": pb.PROBLEM_KIND_BAD_DATE,
    "measurement_mismatch": pb.PROBLEM_KIND_MEASUREMENT_MISMATCH,
    "measurement_unlabeled": pb.PROBLEM_KIND_MEASUREMENT_UNLABELED,
    "invalidated_delegation": pb.PROBLEM_KIND_INVALIDATED_DELEGATION,
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
    "fact_mistake",
    "ignorance_unavoidable",
    "unexpectable",
    "petty_minor",
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
    "fact_mistake",
    "ignorance_unavoidable",
    "unexpectable",
    "petty_minor",
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


def invalidated(i: facts.Invalidated) -> pb.InvalidatedStatement:
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
        scoring_results=[_scoring_result(sr) for sr in result.scoring_results],
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


def _stamp(day: date) -> float:
    return float(day.year * 10000 + day.month * 100 + day.day)


def _number(value: pb.Decimal) -> Decimal | None:
    """有限的十進位數；不是則 None。"""
    try:
        number = Decimal(value.value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def measurements(request: pb.EvaluateRequest) -> dict[str, list[Measurement]]:
    """事實的認定方式；兩者都沒給的事實不列（未提供）。"""
    out: dict[str, list[Measurement]] = {}
    for fact in request.facts:
        if fact.concept and (fact.measurement_method or fact.averaging_period):
            out.setdefault(fact.concept, []).append(
                Measurement(fact.measurement_method or None, fact.averaging_period or None)
            )
    return out


def permit_limits(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> dict[tuple[str, str], list[float]]:
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


def actor_bases(request: pb.EvaluateRequest, validator: errors.Validator) -> list[str]:
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


def _fact_value(value: pb.FactValue, where: str, validator: errors.Validator) -> float | str | None:
    """事實的一個值；不合則記在 `validator` 並回 None。"""
    kind = value.WhichOneof("value")
    if kind == "flag":
        return 1.0 if value.flag else 0.0
    if kind == "concept":
        if not value.concept:
            validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
            return None
        return str(value.concept)
    if kind == "number":
        number = _number(value.number)
        if number is None:
            validator.add(
                f"{where}.number.value", errors.INVALID_NUMBER, "must be a finite decimal number"
            )
            return None
        return float(number)
    validator.add(where, errors.UNSET, "flag, number or concept is required")
    return None


def _fact_values(fact: pb.Fact, where: str, validator: errors.Validator) -> list[float | str]:
    """一筆事實的有效值；概念或值整體不合則記錯並回空。"""
    if not fact.concept:
        validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
        return []
    if fact.concept.startswith(RESERVED_PREFIX):
        validator.add(
            f"{where}.concept",
            errors.RESERVED_CONCEPT,
            f"codes beginning {RESERVED_PREFIX!r} are reserved; use administrative_penalty_facts",
        )
        return []
    if not fact.values:
        validator.add(f"{where}.values", errors.EMPTY, "at least one value is required")
        return []
    decoded = [_fact_value(v, f"{where}.values[{j}]", validator) for j, v in enumerate(fact.values)]
    return [v for v in decoded if v is not None]


def _stated_facts(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> dict[str, list[float | str]]:
    """請求的 `facts`，概念 → 值；同一概念的多筆合併。"""
    several: dict[str, list[float | str]] = {}
    for i, fact in enumerate(request.facts):
        values = _fact_values(fact, f"facts[{i}]", validator)
        if values:
            several.setdefault(fact.concept, []).extend(values)
    return several


def _penalty_decimal(
    penalty: pb.AdministrativePenaltyFacts, name: str, validator: errors.Validator
) -> float | None:
    """行政罰事實的一個非負十進位數；不合則記在 `validator` 並回 None。"""
    where = f"administrative_penalty_facts.{name}"
    number = _number(getattr(penalty, name))
    if number is None:
        validator.add(f"{where}.value", errors.INVALID_NUMBER, "must be a finite decimal number")
        return None
    if number < 0:
        validator.add(where, errors.OUT_OF_RANGE, "must not be negative")
        return None
    return float(number)


def _penalty_facts(
    penalty: pb.AdministrativePenaltyFacts, validator: errors.Validator
) -> dict[str, float]:
    """行政罰法總則的事實，鍵已加保留前綴。"""
    out: dict[str, float] = {}
    for name in _BOOLEAN_FACTS:
        if penalty.HasField(name):
            out[f"{RESERVED_PREFIX}{name}"] = 1.0 if getattr(penalty, name) else 0.0
    if penalty.HasField("age"):
        if penalty.age < 0:
            validator.add(
                "administrative_penalty_facts.age", errors.OUT_OF_RANGE, "age must not be negative"
            )
        else:
            out[f"{RESERVED_PREFIX}age"] = float(penalty.age)
    for decimal_name in _DECIMAL_FACTS:
        if penalty.HasField(decimal_name):
            amount = _penalty_decimal(penalty, decimal_name, validator)
            if amount is not None:
                out[f"{RESERVED_PREFIX}{decimal_name}"] = amount
    return out


def case_facts(
    request: pb.EvaluateRequest,
    validator: errors.Validator,
    conduct_ended: date | None,
    result_on: date | None,
    criminal_cleared: date | None,
) -> dict[str, float | list[float | str]]:
    """請求的案件事實轉成推論程式的輸入。欄位錯誤記在 `validator`，不丟例外。"""
    case: dict[str, float | list[float | str]] = dict(_stated_facts(request, validator))
    case.update(_penalty_facts(request.administrative_penalty_facts, validator))
    for name, day in (
        ("criminal_cleared", criminal_cleared),
        ("conduct_ended", conduct_ended),
        ("result_on", result_on),
    ):
        if day is not None:
            case[f"{RESERVED_PREFIX}{name}"] = _stamp(day)
    return case


def fact_units(request: pb.EvaluateRequest) -> dict[str, str]:
    """萃取各事實所帶之量綱單位代碼。"""
    return {
        str(fact.concept): str(fact.unit)
        for fact in request.facts
        if fact.concept and getattr(fact, "unit", "")
    }


def act_groups(request: pb.EvaluateRequest) -> dict[str, list[int]]:
    """萃取行為分組（§24/§25）。"""
    return {
        str(g.act_id): [int(sid) for sid in g.statement_ids]
        for g in getattr(request, "act_groups", ())
        if g.act_id and g.statement_ids
    }


def timeline(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> PerOccasionTimeline | None:
    """萃取限期改善與複查時點（釋字第 604 號）。"""
    if not hasattr(request, "HasField") or not request.HasField("timeline"):
        return None
    tl = request.timeline

    def _opt_date(raw: str, field: str) -> date | None:
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            validator.add(f"timeline.{field}", errors.INVALID_DATE, "must be ISO 8601 date")
            return None

    return PerOccasionTimeline(
        cure_notice_served_on=_opt_date(tl.cure_notice_served_on, "cure_notice_served_on"),
        cure_deadline=_opt_date(tl.cure_deadline, "cure_deadline"),
        extension_deadline=_opt_date(tl.extension_deadline, "extension_deadline"),
        reinspection_on=_opt_date(tl.reinspection_on, "reinspection_on"),
    )


def scoring_inputs(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> list[scoring.ScoringInput]:
    """將請求內的 `scoring_inputs` 轉成確定性裁罰計算引擎輸入。"""
    out: list[scoring.ScoringInput] = []
    for i, item in enumerate(getattr(request, "scoring_inputs", ())):
        if not item.matrix_code:
            validator.add(f"scoring_inputs[{i}].matrix_code", errors.EMPTY, "matrix_code required")
            continue
        factors: dict[str, Decimal] = {}
        for j, fv in enumerate(item.factors):
            num = _number(fv.value)
            if num is None:
                validator.add(
                    f"scoring_inputs[{i}].factors[{j}].value",
                    errors.INVALID_NUMBER,
                    "must be finite decimal",
                )
            elif fv.factor_code:
                factors[str(fv.factor_code)] = num
        out.append(
            scoring.ScoringInput(
                matrix_code=str(item.matrix_code),
                factors=factors,
                severe_violation=bool(item.severe_violation),
                first_offense_minor_facility=bool(item.first_offense_minor_facility),
                cured_before_notice=bool(item.cured_before_notice),
                is_stop_work=bool(item.is_stop_work),
                repeat_within_year=bool(item.repeat_within_year),
                mitigation_denominator=(
                    int(item.mitigation_denominator)
                    if item.HasField("mitigation_denominator")
                    else None
                ),
                active_benefit=_number(item.active_benefit) or Decimal(0),
                passive_benefit=_number(item.passive_benefit) or Decimal(0),
                criminal_deduction=_number(item.criminal_deduction) or Decimal(0),
            )
        )
    return out


def disposition_audit(
    request: pb.EvaluateRequest, results: Iterable[scoring.ScoringResult]
) -> scoring.DispositionAuditResult | None:
    """依請求內 `disposition_audit` 與對應試算結果執行 CQ D1–D3 處分書結構檢核。"""
    if not hasattr(request, "HasField") or not request.HasField("disposition_audit"):
        return None
    audit_in = request.disposition_audit
    matched = next(
        (r for r in results if r.matrix_code == audit_in.scoring_matrix_code),
        None,
    )
    citations = [
        scoring.DispositionCitation(
            citation_text=str(c.citation_text),
            role=cast(
                Literal[
                    "duty_basis",
                    "delegated_command",
                    "sanction_basis",
                    "discretion_criteria",
                    "general_part",
                ],
                c.role,
            ),
            in_force=bool(c.in_force),
        )
        for c in audit_in.citations
    ]
    elements = [
        scoring.DispositionElement(
            element_name=str(e.element_name),
            tier=cast(
                Literal["tier1_elements", "tier2_unlawfulness", "tier3_culpability"],
                e.tier,
            ),
            fact_summary=str(e.fact_summary),
            evidence_kind=cast(
                Literal[
                    "sampling_report",
                    "cems_record",
                    "inspection_record",
                    "permit_document",
                    "declaration_record",
                    "admission_or_statement",
                    "none",
                ],
                e.evidence_kind,
            ),
            contradicted=bool(e.contradicted),
        )
        for e in audit_in.elements
    ]
    return scoring.audit_disposition(
        citations,
        elements,
        two_tier_defenses_addressed=bool(audit_in.two_tier_defenses_addressed),
        stated_fine=int(audit_in.stated_fine) if audit_in.HasField("stated_fine") else None,
        scoring_result=matched,
    )


def _scoring_result(sr: scoring.ScoringResult) -> pb.ScoringResult:
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
