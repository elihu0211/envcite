"""依裁罰矩陣規格與個案參數做確定性計算。"""

from envcite_ontology.reasoning.scoring.adjustments import apply_general_adjustments
from envcite_ontology.reasoning.scoring.formulas import (
    clamp_ntd,
    compute_raw_bounds,
    resolve_factor,
    round_ntd,
)
from envcite_ontology.reasoning.scoring.models import (
    FactorApplied,
    PenaltyMatrixSpec,
    ScoringInput,
    ScoringResult,
)


def evaluate_matrix(spec: PenaltyMatrixSpec, inp: ScoringInput) -> ScoringResult:
    """依裁罰矩陣規格與個案參數進行確定性計算，絕不憑空猜測未提供之裁量點數。"""
    resolved: list[FactorApplied] = []
    missing: list[str] = []
    judgements: list[str] = []
    notes: list[str] = []

    for fspec in spec.factors:
        applied, is_miss, is_judge = resolve_factor(fspec, inp.factors)
        resolved.append(applied)
        if is_miss:
            missing.append(fspec.factor_code)
        if is_judge:
            judgements.append(fspec.factor_code)

    raw_lo, raw_hi, total_points, demerit_total, demerit_triggered, eq = compute_raw_bounds(
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
        else clamp_ntd(
            round_ntd(raw_lo, spec.rounding_mode),
            spec.statutory_min,
            spec.statutory_max,
            cap_pierced,
        )
    )
    clamped_hi = (
        None
        if raw_hi is None
        else clamp_ntd(
            round_ntd(raw_hi, spec.rounding_mode),
            spec.statutory_min,
            spec.statutory_max,
            cap_pierced,
        )
    )
    adj_lo, adj_hi, lec_lo, lec_hi = apply_general_adjustments(
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
