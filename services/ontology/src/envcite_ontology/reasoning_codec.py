"""推論結果與案件事實在 gRPC 訊息與領域型別之間的轉換。

列舉一律用明確的對照表：資料庫與推論程式的字串若出現表中沒有的值，是 KeyError（內部錯誤），
不悄悄落到 UNSPECIFIED。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from envcite.ontology.v1 import reasoning_pb2 as pb
from envcite_ontology import errors
from envcite_ontology.reasoning import Result
from envcite_ontology.reasoning.engine import Penalty

STATEMENT_KIND = {
    "prescriptive": pb.STATEMENT_KIND_PRESCRIPTIVE,
    "constitutive": pb.STATEMENT_KIND_CONSTITUTIVE,
    "penalty": pb.STATEMENT_KIND_PENALTY,
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
    "unsupported_condition": pb.PROBLEM_KIND_UNSUPPORTED_CONDITION,
    "bad_date": pb.PROBLEM_KIND_BAD_DATE,
}
EXCUSE = {
    "no_fault": pb.EXCUSE_NO_FAULT,
    "under_fourteen": pb.EXCUSE_UNDER_FOURTEEN,
    "incapable": pb.EXCUSE_INCAPABLE,
    "pursuant_to_law": pb.EXCUSE_PURSUANT_TO_LAW,
    "superior_order": pb.EXCUSE_SUPERIOR_ORDER,
    "self_defense": pb.EXCUSE_SELF_DEFENSE,
    "necessity": pb.EXCUSE_NECESSITY,
}
MITIGATION = {
    "youth": pb.MITIGATION_YOUTH,
    "reduced_capacity": pb.MITIGATION_REDUCED_CAPACITY,
    "ignorance": pb.MITIGATION_IGNORANCE,
    "excessive_defense": pb.MITIGATION_EXCESSIVE_DEFENSE,
    "excessive_necessity": pb.MITIGATION_EXCESSIVE_NECESSITY,
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
    for gaps in (result.missing, result.needs_judgement, result.judgement_used):
        ids |= {s for s, _ in gaps}
    for pairs in (result.unresolved_conflicts, result.unresolved_penalty_overlaps):
        ids |= {i for pair in pairs for i in pair}
    ids |= {i for d in result.defeated for i in d[:2]}
    ids |= {i for d in result.penalty_defeated for i in d[:2]}
    ids |= {t.statement_id for t in result.trace} | set(result.limit_sources.values())
    ids |= {p for p, _ in result.general.unpunished} | {p for p, _ in result.general.mitigation}
    ids |= {p for p, _ in result.general.presumed} | set(result.general.time_barred)
    return ids


def _penalty(p: Penalty) -> pb.PenaltyCandidate:
    out = pb.PenaltyCandidate(
        statement_id=p.statement_id,
        punishes=p.punishes_id,
        effect=LEGAL_EFFECT[p.effect],
        conditional=p.conditional,
        per_occasion=p.repeating,
        discretionary=p.discretionary,
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
    result: Result, sources: Mapping[int, str], uncovered: Iterable[str] = ()
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
        ),
        sources=[
            pb.StatementSource(statement_id=i, version_iri=sources[i])
            for i in sorted(statement_ids(result))
            if i in sources
        ],
        uncovered_law_ids=list(uncovered),
    )
    if result.one_act is not None:
        out.one_act.CopyFrom(
            pb.FineInterval(min_amount=result.one_act[0], max_amount=result.one_act[1])
        )
    return out


def _stamp(day: date) -> float:
    return float(day.year * 10000 + day.month * 100 + day.day)


def case_facts(
    request: pb.EvaluateRequest,
    validator: errors.Validator,
    conduct_ended: date | None,
    result_on: date | None,
) -> dict[str, float | list[float]]:
    """請求的案件事實轉成推論程式的輸入。欄位錯誤記在 `validator`，不丟例外。"""
    several: dict[str, list[float]] = {}
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
        values: list[float] = []
        for j, v in enumerate(fact.values):
            kind = v.WhichOneof("value")
            if kind == "flag":
                values.append(1.0 if v.flag else 0.0)
            elif kind == "number":
                try:
                    number = Decimal(v.number.value)
                except InvalidOperation:
                    number = Decimal("NaN")
                if not number.is_finite():
                    validator.add(
                        f"{where}.values[{j}].number.value",
                        errors.INVALID_NUMBER,
                        "must be a finite decimal number",
                    )
                    continue
                values.append(float(number))
            else:
                validator.add(f"{where}.values[{j}]", errors.UNSET, "flag or number is required")
        if values:
            several.setdefault(fact.concept, []).extend(values)
    case: dict[str, float | list[float]] = dict(several)
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
    if conduct_ended is not None:
        case[f"{RESERVED_PREFIX}conduct_ended"] = _stamp(conduct_ended)
    if result_on is not None:
        case[f"{RESERVED_PREFIX}result_on"] = _stamp(result_on)
    return case
