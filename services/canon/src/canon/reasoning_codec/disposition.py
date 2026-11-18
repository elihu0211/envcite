"""處分稽核（`scoring`）的請求與回應轉換。"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from canon import errors
from canon.reasoning.scoring.audit import (
    CITATION_ROLES,
    EVIDENCE_KINDS,
    REVIEW_TIERS,
    DispositionAuditResult,
    DispositionCitation,
    DispositionElement,
    audit_disposition,
)
from canon.reasoning.scoring.models import ScoringInput, ScoringResult
from canon.reasoning_codec.request import finite_number
from envcite.canon.v1 import reasoning_pb2 as pb


def scoring_inputs(request: pb.EvaluateRequest, validator: errors.Validator) -> list[ScoringInput]:
    """將請求內的 `scoring_inputs` 轉成確定性裁罰計算引擎輸入。"""
    out: list[ScoringInput] = []
    for i, item in enumerate(request.scoring_inputs):
        if not item.matrix_code:
            validator.add(f"scoring_inputs[{i}].matrix_code", errors.EMPTY, "matrix_code required")
            continue
        factors: dict[str, Decimal] = {}
        for j, fv in enumerate(item.factors):
            num = finite_number(fv.value)
            if num is None:
                validator.add(
                    f"scoring_inputs[{i}].factors[{j}].value",
                    errors.INVALID_NUMBER,
                    "must be finite decimal",
                )
            elif fv.factor_code:
                factors[str(fv.factor_code)] = num
        out.append(
            ScoringInput(
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
                active_benefit=finite_number(item.active_benefit) or Decimal(0),
                passive_benefit=finite_number(item.passive_benefit) or Decimal(0),
                criminal_deduction=finite_number(item.criminal_deduction) or Decimal(0),
            )
        )
    return out


@dataclass(frozen=True)
class DispositionInput:
    """請求裡的處分書稽核輸入：每個字串欄位都已對上列舉，進得了檢核。"""

    citations: tuple[DispositionCitation, ...]
    elements: tuple[DispositionElement, ...]
    two_tier_defenses_addressed: bool
    stated_fine: int | None
    matrix_code: str


def _member[T](
    validator: errors.Validator, field: str, value: str, allowed: Sequence[T]
) -> T | None:
    """`value` 是 `allowed` 的哪一個成員；不是就記下欄位、原因與可用的值。

    請求裡的這些欄位是自由字串（契約沒有列舉）：未知的值不能當成成員放進檢核，否則會被
    當成「有證據」之類的意思；寧可拒絕並說清楚可以填什麼。
    """
    for member in allowed:
        if member == value:
            return member
    validator.add(
        field,
        errors.EMPTY if not value else errors.UNKNOWN_VALUE,
        f"{field} must be one of: {', '.join(str(a) for a in allowed)}",
    )
    return None


def disposition_input(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> DispositionInput | None:
    """請求內的 `disposition_audit`；沒有就回 `None`，欄位不合的記進 `validator`。"""
    if not hasattr(request, "HasField") or not request.HasField("disposition_audit"):
        return None
    audit_in = request.disposition_audit
    citations: list[DispositionCitation] = []
    for i, c in enumerate(audit_in.citations):
        role = _member(validator, f"disposition_audit.citations[{i}].role", c.role, CITATION_ROLES)
        if role is not None:
            citations.append(
                DispositionCitation(
                    citation_text=str(c.citation_text), role=role, in_force=bool(c.in_force)
                )
            )
    elements: list[DispositionElement] = []
    for i, e in enumerate(audit_in.elements):
        tier = _member(validator, f"disposition_audit.elements[{i}].tier", e.tier, REVIEW_TIERS)
        evidence = _member(
            validator,
            f"disposition_audit.elements[{i}].evidence_kind",
            e.evidence_kind,
            EVIDENCE_KINDS,
        )
        if tier is not None and evidence is not None:
            elements.append(
                DispositionElement(
                    element_name=str(e.element_name),
                    tier=tier,
                    fact_summary=str(e.fact_summary),
                    evidence_kind=evidence,
                    contradicted=bool(e.contradicted),
                )
            )
    return DispositionInput(
        citations=tuple(citations),
        elements=tuple(elements),
        two_tier_defenses_addressed=bool(audit_in.two_tier_defenses_addressed),
        stated_fine=int(audit_in.stated_fine) if audit_in.HasField("stated_fine") else None,
        matrix_code=str(audit_in.scoring_matrix_code),
    )


def disposition_audit(
    audit: DispositionInput | None, results: Iterable[ScoringResult]
) -> DispositionAuditResult | None:
    """依處分書稽核輸入與對應試算結果執行 CQ D1–D3 處分書結構檢核。"""
    if audit is None:
        return None
    matched = next((r for r in results if r.matrix_code == audit.matrix_code), None)
    return audit_disposition(
        audit.citations,
        audit.elements,
        two_tier_defenses_addressed=audit.two_tier_defenses_addressed,
        stated_fine=audit.stated_fine,
        scoring_result=matched,
    )
