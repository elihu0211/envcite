"""環境法裁罰積分／倍數／記點計算引擎、環境教育講習時數矩陣與處分書要件檢核（01-08）。

本模組實作台灣環境法三大法定計分結構，且維持「規則與矩陣是資料、引擎不寫死個案」不變式：
  1. 點數基數相乘制（《違反水污染防治法罰鍰額度裁罰準則》§§2–6）：
     處分點數 = 違規態樣點數 + 加重點數 - 減輕點數；罰鍰 = floor(處分點數 × 處分基數)。
  2. 權重因子相乘制（《固定污染源空污裁罰準則》A×B×C×D×(1+E)×下限、
     《廢清裁罰準則》最低罰鍰×A×B×C[×資力倍數]、《毒管裁罰準則》A×B×C×基數）。
  3. 缺失記點累積制（《營建工程空污防制設施缺失記點及處理原則》）：
     累計缺失點數 >= 10 點認定違規明確並觸發罰鍰＋限期改善＋環境講習。
  4. 環境講習時數矩陣（《環境教育法》§23、《環境講習執行辦法》§8）：
     依罰鍰占法定最高額比例 A（<=35% -> 2h；35%<A<=70% -> 4h；>70% -> 8h）、
     停工停業（8h）及一年內同條累犯（加倍，上限 8h）確定性導出。
  5. 裁罰處分書結構檢核（專論 01-08 CQ D1–D3）。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal, NamedTuple

from envcite_ontology.db import Conn, fetch

type FormulaKind = Literal[
    "additive_points_times_base",
    "multiplicative_factors",
    "demerit_accumulation",
    "fixed_schedule",
]
type FactorRole = Literal[
    "base_point",
    "aggravation_point",
    "mitigation_point",
    "multiplicative_factor",
    "additive_rate",
    "demerit_point",
    "fixed_amount",
]


@dataclass(frozen=True)
class FactorSpec:
    """矩陣單一因子規格（對應 `scoring.matrix_factor`）。"""

    factor_code: str
    factor_role: FactorRole
    label_zho: str
    statutory_ref: str
    default_value: Decimal | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    needs_judgement: bool = False
    description: str = ""


@dataclass(frozen=True)
class PenaltyMatrixSpec:
    """環境裁罰矩陣規格（對應 `scoring.penalty_matrix`）。"""

    matrix_code: str
    law_title: str
    norm_kind: Literal["delegated_regulation", "discretion_standard"]
    formula_kind: FormulaKind
    legal_effect: str = "fine"
    base_amount: Decimal | None = None
    statutory_min: Decimal | None = None
    statutory_max: Decimal | None = None
    demerit_threshold: Decimal | None = None
    rounding_mode: Literal["floor_ntd", "round_ntd", "ceil_ntd", "none"] = "floor_ntd"
    factors: tuple[FactorSpec, ...] = ()
    provision_id: int | None = None
    statutory_basis: str = ""


@dataclass(frozen=True)
class ScoringInput:
    """呼叫端提供給裁罰矩陣的個案量化／情節因子與總則調整參數。"""

    matrix_code: str
    factors: Mapping[str, Decimal | float | int] = field(default_factory=dict)
    severe_violation: bool = False
    first_offense_minor_facility: bool = False
    cured_before_notice: bool = False
    is_stop_work: bool = False
    repeat_within_year: bool = False
    mitigation_denominator: int | None = None
    active_benefit: Decimal = Decimal(0)
    passive_benefit: Decimal = Decimal(0)
    criminal_deduction: Decimal = Decimal(0)


@dataclass(frozen=True)
class FactorApplied:
    """計算明細中已套用或待定之單一因子。"""

    factor_code: str
    label_zho: str
    factor_role: FactorRole
    statutory_ref: str
    value: Decimal | None
    interval_min: Decimal | None
    interval_max: Decimal | None
    missing: bool
    needs_judgement: bool


@dataclass(frozen=True)
class ScoringResult:
    """裁罰積分／倍數／記點與環境講習的確定性計算結果。"""

    matrix_code: str
    law_title: str
    norm_kind: str
    formula_kind: FormulaKind
    equation_summary: str
    factors_applied: tuple[FactorApplied, ...]
    raw_min: Decimal | None
    raw_max: Decimal | None
    clamped_min: int | None
    clamped_max: int | None
    adjusted_min: int | None
    adjusted_max: int | None
    total_points: Decimal | None = None
    demerit_total: Decimal | None = None
    demerit_triggered: bool | None = None
    total_illegal_benefit: Decimal = Decimal(0)
    cap_pierced: bool = False
    lecture_hours_min: int = 0
    lecture_hours_max: int = 0
    missing_factors: tuple[str, ...] = ()
    judgement_factors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _round_ntd(value: Decimal, mode: str) -> int:
    if mode == "floor_ntd":
        return int(value.to_integral_value(rounding=ROUND_FLOOR))
    if mode == "ceil_ntd":
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    if mode == "round_ntd":
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))
    return int(value)


def _clamp_ntd(
    value: int,
    stat_min: Decimal | None,
    stat_max: Decimal | None,
    cap_pierced: bool,
) -> int:
    out = value
    if stat_min is not None:
        out = max(out, int(stat_min))
    if stat_max is not None and not cap_pierced:
        out = min(out, int(stat_max))
    return max(0, out)


def compute_lecture_hours(
    fine_amount: int | None,
    statutory_max: Decimal | None,
    *,
    is_stop_work: bool = False,
    repeat_within_year: bool = False,
) -> tuple[int, int]:
    """依《環境教育法》§23 與《環境講習執行辦法》§8 計算應受環境講習時數區間。"""
    if is_stop_work:
        return (8, 8)
    if fine_amount is None:
        return (0, 8)
    if fine_amount < 5000:
        return (0, 0)
    if fine_amount <= 10000:
        base = 1
        hours = min(8, base * 2) if repeat_within_year else base
        return (hours, hours)
    if statutory_max is None or statutory_max <= 0:
        return (2, 8)
    ratio = Decimal(fine_amount) / statutory_max
    if ratio <= Decimal("0.35"):
        base = 2
    elif ratio <= Decimal("0.70"):
        base = 4
    else:
        base = 8
    hours = min(8, base * 2) if repeat_within_year else base
    return (hours, hours)


def _resolve_factor(
    spec: FactorSpec,
    given: Mapping[str, Decimal | float | int],
) -> tuple[FactorApplied, bool, bool]:
    raw = given.get(spec.factor_code)
    if raw is not None:
        val = Decimal(str(raw))
        applied = FactorApplied(
            factor_code=spec.factor_code,
            label_zho=spec.label_zho,
            factor_role=spec.factor_role,
            statutory_ref=spec.statutory_ref,
            value=val,
            interval_min=val,
            interval_max=val,
            missing=False,
            needs_judgement=spec.needs_judgement,
        )
        return applied, False, False
    if spec.default_value is not None and not spec.needs_judgement:
        val = spec.default_value
        applied = FactorApplied(
            factor_code=spec.factor_code,
            label_zho=spec.label_zho,
            factor_role=spec.factor_role,
            statutory_ref=spec.statutory_ref,
            value=val,
            interval_min=val,
            interval_max=val,
            missing=False,
            needs_judgement=False,
        )
        return applied, False, False
    applied = FactorApplied(
        factor_code=spec.factor_code,
        label_zho=spec.label_zho,
        factor_role=spec.factor_role,
        statutory_ref=spec.statutory_ref,
        value=None,
        interval_min=spec.min_value,
        interval_max=spec.max_value,
        missing=not spec.needs_judgement,
        needs_judgement=spec.needs_judgement,
    )
    return applied, not spec.needs_judgement, spec.needs_judgement


def _eval_additive(
    spec: PenaltyMatrixSpec,
    items: Sequence[FactorApplied],
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
    base = spec.base_amount or Decimal(0)
    lo_pts = Decimal(0)
    hi_pts = Decimal(0)
    exact = True
    for item in items:
        if item.interval_min is None or item.interval_max is None:
            exact = False
            continue
        if item.factor_role in ("base_point", "aggravation_point"):
            lo_pts += item.interval_min
            hi_pts += item.interval_max
        elif item.factor_role == "mitigation_point":
            lo_pts -= item.interval_max
            hi_pts -= item.interval_min
    lo_pts = max(Decimal(1), lo_pts) if exact else lo_pts
    hi_pts = max(Decimal(1), hi_pts) if exact else hi_pts
    if not exact:
        return None, None, None, "處分點數 × 處分基數（部分點數待補或待裁量）"
    raw_lo = lo_pts * base
    raw_hi = hi_pts * base
    mid_pts = lo_pts if lo_pts == hi_pts else None
    summary = f"處分點數 ({lo_pts}..{hi_pts}) × 處分基數 ({base})"
    return raw_lo, raw_hi, mid_pts, summary


def _eval_multiplicative(
    spec: PenaltyMatrixSpec,
    items: Sequence[FactorApplied],
) -> tuple[Decimal | None, Decimal | None, str]:
    anchor = spec.base_amount if spec.base_amount is not None else spec.statutory_min
    if anchor is None:
        return None, None, "缺乏基準罰鍰／下限金額"
    mult_lo = Decimal(1)
    mult_hi = Decimal(1)
    rate_lo = Decimal(0)
    rate_hi = Decimal(0)
    for item in items:
        if item.interval_min is None or item.interval_max is None:
            return None, None, "乘數或加減因子未提供且無上下界，不猜測額度"
        if item.factor_role == "multiplicative_factor":
            mult_lo *= item.interval_min
            mult_hi *= item.interval_max
        elif item.factor_role == "additive_rate":
            rate_lo += item.interval_min
            rate_hi += item.interval_max
    factor_e_lo = max(Decimal("0.0"), Decimal(1) + rate_lo)
    factor_e_hi = max(Decimal("0.0"), Decimal(1) + rate_hi)
    return (
        anchor * mult_lo * factor_e_lo,
        anchor * mult_hi * factor_e_hi,
        f"基準 ({anchor}) × 乘數 ({mult_lo}..{mult_hi}) × (1 + E[{rate_lo}..{rate_hi}])",
    )


def _eval_demerit(
    spec: PenaltyMatrixSpec,
    items: Sequence[FactorApplied],
) -> tuple[Decimal, Decimal, bool, str]:
    lo_sum = Decimal(0)
    hi_sum = Decimal(0)
    for item in items:
        if item.factor_role == "demerit_point":
            if item.interval_min is not None:
                lo_sum += item.interval_min
            if item.interval_max is not None:
                hi_sum += item.interval_max
    threshold = spec.demerit_threshold or Decimal(10)
    triggered = lo_sum >= threshold
    status = (
        "達違規認定門檻（應告發處分＋限期改善）"
        if triggered
        else "未達直接裁處門檻（勸導／限期改善）"
    )
    return lo_sum, hi_sum, triggered, f"缺失記點合計 {lo_sum} 點（門檻 {threshold} 點：{status}）"


def _compute_raw_bounds(
    spec: PenaltyMatrixSpec,
    resolved: Sequence[FactorApplied],
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, bool | None, str]:
    if spec.formula_kind == "additive_points_times_base":
        lo, hi, pts, eq = _eval_additive(spec, resolved)
        return lo, hi, pts, None, None, eq
    if spec.formula_kind == "multiplicative_factors":
        lo, hi, eq = _eval_multiplicative(spec, resolved)
        return lo, hi, None, None, None, eq
    if spec.formula_kind == "demerit_accumulation":
        d_lo, d_hi, triggered, eq = _eval_demerit(spec, resolved)
        dem_total = d_lo if d_lo == d_hi else d_hi
        if triggered and spec.statutory_min is not None:
            hi_bound = spec.statutory_max or spec.statutory_min
            return spec.statutory_min, hi_bound, None, dem_total, triggered, eq
        if not triggered:
            return Decimal(0), Decimal(0), None, dem_total, triggered, eq
        return None, None, None, dem_total, triggered, eq
    fixed = [
        r.interval_min
        for r in resolved
        if r.factor_role == "fixed_amount" and r.interval_min is not None
    ]
    lo = sum(fixed, Decimal(0)) if fixed else spec.statutory_min
    return lo, lo, None, None, None, f"定額附表裁處金額 {lo}"


def _apply_general_adjustments(
    spec: PenaltyMatrixSpec,
    inp: ScoringInput,
    clamped_lo: int | None,
    clamped_hi: int | None,
    notes: list[str],
) -> tuple[int | None, int | None, int, int]:
    adj_lo, adj_hi = clamped_lo, clamped_hi
    if inp.mitigation_denominator in (2, 3) and adj_lo is not None and adj_hi is not None:
        k = inp.mitigation_denominator
        adj_lo = (adj_lo + k - 1) // k
        adj_hi = adj_hi // k
        notes.append(f"行政罰法 §18 III 減輕（1/{k} 界限）")
    if inp.criminal_deduction > 0 and adj_lo is not None and adj_hi is not None:
        ded = int(inp.criminal_deduction)
        adj_lo = max(0, adj_lo - ded)
        adj_hi = max(0, adj_hi - ded)
        notes.append(f"行政罰法 §26 III/IV 刑事支付或勞務扣抵 {ded} 元")
    lec_lo, lec_hi = compute_lecture_hours(
        adj_lo,
        spec.statutory_max,
        is_stop_work=inp.is_stop_work,
        repeat_within_year=inp.repeat_within_year,
    )
    if adj_hi is not None and adj_hi != adj_lo:
        _, lec_hi_upper = compute_lecture_hours(
            adj_hi,
            spec.statutory_max,
            is_stop_work=inp.is_stop_work,
            repeat_within_year=inp.repeat_within_year,
        )
        lec_hi = max(lec_hi, lec_hi_upper)
    return adj_lo, adj_hi, lec_lo, lec_hi


def evaluate_matrix(spec: PenaltyMatrixSpec, inp: ScoringInput) -> ScoringResult:
    """依裁罰矩陣規格與個案參數進行確定性計算，絕不憑空猜測未提供之裁量點數。"""
    resolved: list[FactorApplied] = []
    missing: list[str] = []
    judgements: list[str] = []
    notes: list[str] = []

    for fspec in spec.factors:
        applied, is_miss, is_judge = _resolve_factor(fspec, inp.factors)
        resolved.append(applied)
        if is_miss:
            missing.append(fspec.factor_code)
        if is_judge:
            judgements.append(fspec.factor_code)

    raw_lo, raw_hi, total_points, demerit_total, demerit_triggered, eq = _compute_raw_bounds(
        spec, resolved
    )
    if inp.severe_violation and spec.statutory_max is not None:
        raw_lo = raw_hi = spec.statutory_max
        notes.append("情節嚴重／重大：依法得逕以法定最高額裁處")
    elif (
        inp.first_offense_minor_facility or inp.cured_before_notice
    ) and spec.statutory_min is not None:
        raw_lo = raw_hi = spec.statutory_min
        notes.append("適用首次違規或通知前補正條款：改以法定最低額裁處")

    total_benefit = inp.active_benefit + inp.passive_benefit
    cap_pierced = bool(spec.statutory_max is not None and total_benefit > spec.statutory_max)
    if cap_pierced:
        notes.append(
            f"所得利益 {total_benefit} 元逾法定最高額 {spec.statutory_max} 元"
            "（行政罰法 §18 II 得突破上限）"
        )

    clamped_lo = (
        None
        if raw_lo is None
        else _clamp_ntd(
            _round_ntd(raw_lo, spec.rounding_mode),
            spec.statutory_min,
            spec.statutory_max,
            cap_pierced,
        )
    )
    clamped_hi = (
        None
        if raw_hi is None
        else _clamp_ntd(
            _round_ntd(raw_hi, spec.rounding_mode),
            spec.statutory_min,
            spec.statutory_max,
            cap_pierced,
        )
    )
    adj_lo, adj_hi, lec_lo, lec_hi = _apply_general_adjustments(
        spec, inp, clamped_lo, clamped_hi, notes
    )

    return ScoringResult(
        matrix_code=spec.matrix_code,
        law_title=spec.law_title,
        norm_kind=spec.norm_kind,
        formula_kind=spec.formula_kind,
        equation_summary=eq,
        factors_applied=tuple(resolved),
        raw_min=raw_lo,
        raw_max=raw_hi,
        clamped_min=clamped_lo,
        clamped_max=clamped_hi,
        adjusted_min=adj_lo,
        adjusted_max=adj_hi,
        total_points=total_points,
        demerit_total=demerit_total,
        demerit_triggered=demerit_triggered,
        total_illegal_benefit=total_benefit,
        cap_pierced=cap_pierced,
        lecture_hours_min=lec_lo,
        lecture_hours_max=lec_hi,
        missing_factors=tuple(missing),
        judgement_factors=tuple(judgements),
        notes=tuple(notes),
    )


class _MatrixRow(NamedTuple):
    id: int
    matrix_code: str
    law_title: str
    norm_kind: Literal["delegated_regulation", "discretion_standard"]
    formula_kind: FormulaKind
    legal_effect: str
    base_amount: Decimal | None
    statutory_min: Decimal | None
    statutory_max: Decimal | None
    demerit_threshold: Decimal | None
    rounding_mode: Literal["floor_ntd", "round_ntd", "ceil_ntd", "none"]
    provision_id: int
    statutory_basis: str


class _FactorRow(NamedTuple):
    matrix_id: int
    factor_code: str
    factor_role: FactorRole
    label_zho: str
    statutory_ref: str
    default_value: Decimal | None
    min_value: Decimal | None
    max_value: Decimal | None
    needs_judgement: bool
    description: str


def load_matrices(conn: Conn, as_of: date, language: str = "zho") -> dict[str, PenaltyMatrixSpec]:
    """自 PostgreSQL `scoring.penalty_matrix` 載入 `as_of` 當日有效條文所錨定之裁罰矩陣。"""
    rows = fetch(
        conn,
        """
        SELECT m.id, m.matrix_code, m.law_title, m.norm_kind, m.formula_kind::text,
               m.legal_effect::text, m.base_amount, m.statutory_min, m.statutory_max,
               m.demerit_threshold, m.rounding_mode, m.provision_id,
               coalesce(m.metadata->>'statutory_basis', '')
          FROM scoring.penalty_matrix AS m
          JOIN corpus.provision AS p ON p.id = m.provision_id
          JOIN corpus.provision_version AS v
            ON v.provision_id = m.provision_id AND v.content_sha256 = m.content_sha256
           AND v.superseded_at IS NULL AND v.force_period @> %s
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id AND e.language = %s
         ORDER BY m.matrix_code
        """,
        (as_of, language),
        _MatrixRow,
    )
    if not rows:
        return {}
    matrix_ids = [r.id for r in rows]
    factor_rows = fetch(
        conn,
        """
        SELECT matrix_id, factor_code, factor_role::text, label_zho, statutory_ref,
               default_value, min_value, max_value, needs_judgement, description
          FROM scoring.matrix_factor
         WHERE matrix_id = ANY(%s)
         ORDER BY matrix_id, id
        """,
        (matrix_ids,),
        _FactorRow,
    )
    by_matrix: dict[int, list[FactorSpec]] = {}
    for fr in factor_rows:
        by_matrix.setdefault(fr.matrix_id, []).append(
            FactorSpec(
                factor_code=fr.factor_code,
                factor_role=fr.factor_role,
                label_zho=fr.label_zho,
                statutory_ref=fr.statutory_ref,
                default_value=fr.default_value,
                min_value=fr.min_value,
                max_value=fr.max_value,
                needs_judgement=fr.needs_judgement,
                description=fr.description,
            )
        )
    return {
        r.matrix_code: PenaltyMatrixSpec(
            matrix_code=r.matrix_code,
            law_title=r.law_title,
            norm_kind=r.norm_kind,
            formula_kind=r.formula_kind,
            legal_effect=r.legal_effect,
            base_amount=r.base_amount,
            statutory_min=r.statutory_min,
            statutory_max=r.statutory_max,
            demerit_threshold=r.demerit_threshold,
            rounding_mode=r.rounding_mode,
            factors=tuple(by_matrix.get(r.id, ())),
            provision_id=r.provision_id,
            statutory_basis=r.statutory_basis,
        )
        for r in rows
    }


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
