"""裁罰矩陣的規格與計算的輸入、結果：資料型別，不含計算。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

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
