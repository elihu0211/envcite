"""三種計分結構的公式：因子解析、點數基數相乘、權重因子相乘、缺失記點，與金額進位及上下限。"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from envcite_ontology.reasoning.scoring.models import FactorApplied, FactorSpec, PenaltyMatrixSpec


def round_ntd(value: Decimal, mode: str) -> int:
    """依進位方式（`floor_ntd`、`ceil_ntd`、`round_ntd`，其餘取整數部分）換成新臺幣整數。"""
    if mode == "floor_ntd":
        return int(value.to_integral_value(rounding=ROUND_FLOOR))
    if mode == "ceil_ntd":
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    if mode == "round_ntd":
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))
    return int(value)


def clamp_ntd(
    value: int,
    stat_min: Decimal | None,
    stat_max: Decimal | None,
    cap_pierced: bool,
) -> int:
    """金額夾在法定下限與上限之間（`cap_pierced` 為真時不設上限），且不為負。"""
    out = value
    if stat_min is not None:
        out = max(out, int(stat_min))
    if stat_max is not None and not cap_pierced:
        out = min(out, int(stat_max))
    return max(0, out)


def resolve_factor(
    spec: FactorSpec,
    given: Mapping[str, Decimal | float | int],
) -> tuple[FactorApplied, bool, bool]:
    """以呼叫端給的值解析一個因子；回傳（套用結果，是否缺值，是否待判斷）。沒給值的取規格的區間，不猜點數。"""
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


def compute_raw_bounds(
    spec: PenaltyMatrixSpec,
    resolved: Sequence[FactorApplied],
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, bool | None, str]:
    """依公式種類算出未夾限的金額下限與上限；回傳（下限，上限，處分點數，缺失點數，是否觸發，算式說明）。"""
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
