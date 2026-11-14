"""裁罰處分書結構完備性檢核（專論 01-08 CQ D1–D3）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from envcite_ontology.reasoning.scoring.models import ScoringResult


@dataclass(frozen=True)
class DispositionCitation:
    """處分書所引法條及其功能（專論 01-08 CQ D1）。"""

    citation_text: str
    role: Literal[
        "duty_basis",
        "delegated_command",
        "sanction_basis",
        "discretion_criteria",
        "general_part",
    ]
    in_force: bool = True


@dataclass(frozen=True)
class DispositionElement:
    """處分書之構成要件／三階審查與證據對應（專論 01-08 CQ D2）。"""

    element_name: str
    tier: Literal["tier1_elements", "tier2_unlawfulness", "tier3_culpability"]
    fact_summary: str
    evidence_kind: Literal[
        "sampling_report",
        "cems_record",
        "inspection_record",
        "permit_document",
        "declaration_record",
        "admission_or_statement",
        "none",
    ]
    contradicted: bool = False


@dataclass(frozen=True)
class DispositionAuditResult:
    """裁罰處分書結構完備性檢核結果（對應 CQ D1, D2, D3）。"""

    d1_citations_complete: bool
    d2_evidence_grounded: bool
    d3_defense_and_bounds_valid: bool
    findings: tuple[str, ...]


def audit_disposition(
    citations: Sequence[DispositionCitation],
    elements: Sequence[DispositionElement],
    *,
    two_tier_defenses_addressed: bool,
    stated_fine: int | None = None,
    scoring_result: ScoringResult | None = None,
) -> DispositionAuditResult:
    """執行專論 01-08 能力問題 D1–D3 之處分書法學結構檢核。"""
    findings: list[str] = []
    roles = {c.role for c in citations if c.in_force}
    d1 = "duty_basis" in roles and "sanction_basis" in roles
    if "duty_basis" not in roles:
        findings.append("D1 缺漏：未引用現行有效之行為／義務依據條文（duty_basis）")
    if "sanction_basis" not in roles:
        findings.append("D1 缺漏：未引用現行有效之罰則依據條文（sanction_basis）")
    if any(not c.in_force for c in citations):
        d1 = False
        findings.append("D1 瑕疵：處分書引用了於行為時或裁處時尚未生效／已廢止之條文")

    ungrounded = [
        e.element_name for e in elements if e.evidence_kind == "none" or not e.fact_summary.strip()
    ]
    contradicted = [e.element_name for e in elements if e.contradicted]
    d2 = bool(elements) and not ungrounded and not contradicted
    if not elements:
        findings.append("D2 缺漏：未提出任何構成要件與事實論理")
    if ungrounded:
        findings.append(f"D2 證據不足：下列要件缺乏具體事實或證據方法：{', '.join(ungrounded)}")
    if contradicted:
        findings.append(f"D2 事實矛盾：下列要件存在未消解之證據衝突：{', '.join(contradicted)}")

    d3 = two_tier_defenses_addressed
    if not two_tier_defenses_addressed:
        findings.append("D3 缺漏：未就阻卻違法（§§11–13）與有責性／但書抗辯（§§7–9）逐一論理")
    if stated_fine is not None and scoring_result is not None:
        lo = scoring_result.adjusted_min
        hi = scoring_result.adjusted_max
        if lo is not None and hi is not None and not (lo <= stated_fine <= hi):
            d3 = False
            findings.append(
                f"D3 裁量逸脫／濫用風險：處分金額 {stated_fine} 元"
                f"落於法定裁量區間 [{lo}, {hi}] 之外"
            )

    return DispositionAuditResult(
        d1_citations_complete=d1,
        d2_evidence_grounded=d2,
        d3_defense_and_bounds_valid=d3,
        findings=tuple(findings),
    )
