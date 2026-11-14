"""矩陣之外的一般調整（行政罰法減輕與刑事扣抵）與環境講習時數。"""

from decimal import Decimal

from envcite_ontology.reasoning.scoring.models import PenaltyMatrixSpec, ScoringInput


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


def apply_general_adjustments(
    spec: PenaltyMatrixSpec,
    inp: ScoringInput,
    clamped_lo: int | None,
    clamped_hi: int | None,
    notes: list[str],
) -> tuple[int | None, int | None, int, int]:
    """套用行政罰法的減輕與刑事扣抵，並算環境講習時數，適用的條文記入 `notes`。

    回傳（金額下限，金額上限，時數下限，時數上限）。
    """
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
