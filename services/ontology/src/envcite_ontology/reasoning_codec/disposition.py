"""處分稽核（`scoring`）的請求與回應轉換。"""

from collections.abc import Iterable
from decimal import Decimal
from typing import Literal, cast

from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology import errors
from envcite_ontology.reasoning.scoring.audit import (
    DispositionAuditResult,
    DispositionCitation,
    DispositionElement,
    audit_disposition,
)
from envcite_ontology.reasoning.scoring.models import ScoringInput, ScoringResult
from envcite_ontology.reasoning_codec.request import finite_number


def scoring_inputs(request: pb.EvaluateRequest, validator: errors.Validator) -> list[ScoringInput]:
    """將請求內的 `scoring_inputs` 轉成確定性裁罰計算引擎輸入。"""
    out: list[ScoringInput] = []
    for i, item in enumerate(getattr(request, "scoring_inputs", ())):
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


def disposition_audit(
    request: pb.EvaluateRequest, results: Iterable[ScoringResult]
) -> DispositionAuditResult | None:
    """依請求內 `disposition_audit` 與對應試算結果執行 CQ D1–D3 處分書結構檢核。"""
    if not hasattr(request, "HasField") or not request.HasField("disposition_audit"):
        return None
    audit_in = request.disposition_audit
    matched = next(
        (r for r in results if r.matrix_code == audit_in.scoring_matrix_code),
        None,
    )
    citations = [
        DispositionCitation(
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
        DispositionElement(
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
    return audit_disposition(
        citations,
        elements,
        two_tier_defenses_addressed=bool(audit_in.two_tier_defenses_addressed),
        stated_fine=int(audit_in.stated_fine) if audit_in.HasField("stated_fine") else None,
        scoring_result=matched,
    )
