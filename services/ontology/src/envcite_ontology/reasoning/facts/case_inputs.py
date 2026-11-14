"""案件端的事實：請求給的值、認定方式、裁處權時效與按次處罰時點，寫成 ASP 事實；不碰資料庫。"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from envcite_ontology.db import Conn
from envcite_ontology.reasoning import units
from envcite_ontology.reasoning.facts.asp import milli, q

# 案件事實的值：數字，或概念代碼（概念取某個值概念）；同一概念可帶多個值。
type CaseValue = float | str

type Case = Mapping[str, CaseValue | Sequence[CaseValue]]


@dataclass(frozen=True)
class Measurement:
    """案件事實的認定方式與物理單位：檢測方法、平均時段與量測單位；None 是未提供。"""

    method: str | None = None
    averaging: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class PerOccasionTimeline:
    """按次處罰與限期改善狀態機時點（釋字第 604 號）。"""

    cure_notice_served_on: date | None = None
    cure_deadline: date | None = None
    extension_deadline: date | None = None
    reinspection_on: date | None = None


#: 事實概念 → 它的值所用的認定方式（同一概念可由數筆事實提供，各帶認定方式）。
type Measurements = Mapping[str, Sequence[Measurement]]

#: （許可種類，概念）→ 該場所持有的這種許可所載、這個概念的量值（委由個別處分的門檻值）。
type Permits = Mapping[tuple[str, str], Sequence[float]]


# 以元計的金額與時數：clingo 只有 32 位元整數，千分位的金額會溢位，所以不進推論程式，由
# `amounts` 在 Python 比較與計算。
_AMOUNTS = (
    "ap_gained_benefit",
    "ap_criminal_payment",
    "ap_community_service_hours",
    "ap_minimum_hourly_wage",
)


@dataclass(frozen=True)
class Amounts:
    """第 18 條第 2 項與第 26 條第 3、4 項所需的金額，已在 Python 算好。"""

    #: 所得利益（元）；未提供為 None
    benefit: Decimal | None
    #: 應扣抵的金額（元）；沒有支付也沒有勞務、或有時數而沒有工資，為 None
    deduction: Decimal | None
    #: 有勞務時數而沒有基本工資
    wage_missing: bool


def amounts(case: Case) -> Amounts:
    """第 18、26 條所需的金額：所得利益，與刑事已支付額加勞務時數乘基本工資的扣抵額。

    字串（概念值）或多值的事實不當數字，視為未提供。有時數而沒有基本工資時不算扣抵額，
    `wage_missing` 為真。
    """

    def number(code: str) -> Decimal | None:
        raw = case.get(code)
        return None if raw is None or isinstance(raw, str | Sequence) else Decimal(str(raw))

    payment = number("ap_criminal_payment")
    hours = number("ap_community_service_hours")
    wage = number("ap_minimum_hourly_wage")
    wage_missing = hours is not None and wage is None
    deduction: Decimal | None = None
    if not wage_missing and (payment is not None or hours is not None):
        deduction = (payment or Decimal(0)) + (
            hours * wage if hours is not None and wage is not None else Decimal(0)
        )
    return Amounts(number("ap_gained_benefit"), deduction, wage_missing)


def amount_facts(given: Amounts, fine_caps: Mapping[int, int]) -> str:
    """金額的比較結果寫成 ASP 事實：利益超過哪些罰鍰的最高額、扣抵額是否已定。"""
    out: list[str] = []
    if given.benefit is not None:
        out += [
            f"benefit_over({p})." for p, cap in sorted(fine_caps.items()) if given.benefit > cap
        ]
    if given.deduction is not None:
        out.append("deduction_given.")
    if given.wage_missing:
        out.append("wage_missing.")
    return "\n".join(out)


def normalize_case_units(
    conn: Conn,
    case: Case,
    measurements: Measurements | None = None,
    fact_units: Mapping[str, str] | None = None,
) -> tuple[Case, list[str]]:
    """依詞彙標準量綱單位換算案件數值；物理量綱不相容者排除該數值並回報 `unit_mismatch` 問題。"""
    if not measurements and not fact_units:
        return case, []
    canonical = {
        str(code): str(unit)
        for code, unit in conn.execute(
            "SELECT code, canonical_unit FROM vocab.concept WHERE canonical_unit IS NOT NULL"
        )
    }
    if not canonical:
        return case, []
    normalized: dict[str, CaseValue | Sequence[CaseValue]] = dict(case)
    problems: list[str] = []
    for code, target_unit in canonical.items():
        if code not in normalized:
            continue
        raw = normalized[code]
        if isinstance(raw, str | Sequence) and not isinstance(raw, int | float):
            continue
        src_unit = (fact_units or {}).get(code)
        if src_unit is None and measurements and code in measurements:
            unit_candidates = [m.unit for m in measurements[code] if m.unit is not None]
            if unit_candidates:
                src_unit = unit_candidates[0]
        if src_unit is None:
            continue
        converted, ok = units.normalize_value(raw, src_unit, target_unit)
        if not ok or converted is None:
            problems.append(f"problem(unit_mismatch,{q(code)}).")
            normalized.pop(code)
        else:
            normalized[code] = float(converted)
    return normalized, problems


def act_group_facts(act_groups: Mapping[str, Sequence[int]] | None) -> str:
    """行為分組（行政罰法 §§24、25）：將陳述編號歸屬至具體行為識別碼 `act_id`。"""
    if not act_groups:
        return ""
    lines: list[str] = []
    for act_id, stmt_ids in sorted(act_groups.items()):
        for sid in stmt_ids:
            lines.append(f"act_stmt({q(act_id)},{int(sid)}).")
    return "\n".join(lines)


def per_occasion_facts(timeline: PerOccasionTimeline | None) -> str:
    """按次處罰與限期改善狀態機事實（釋字第 604 號）。"""
    if timeline is None or timeline.reinspection_on is None:
        return ""
    effective_deadline = timeline.extension_deadline or timeline.cure_deadline
    if effective_deadline is None:
        return ""
    if timeline.reinspection_on > effective_deadline:
        return "order_lapsed_verified."
    return "within_cure_grace_period."


def case_facts(
    case: Case,
    measurements: Measurements | None = None,
    permits: Permits | None = None,
    actor_bases: Collection[str] | None = None,
) -> str:
    """案件事實：概念代碼 → 值（同一概念可有多個值，矛盾由推論程式檢出）。

    數字是千分位的 `fact_in`；字串是概念值（行業屬某行業），寫成 `cfact_in`。認定方式寫成
    `fmethod`／`favg`；許可所載的量值寫成 `permit_in`（同一許可、同一概念有兩個值即矛盾）。
    行為人所屬的責任基礎（`act`、`state`、`representative`，可同時屬數種）寫成 `actor_basis`；
    沒給與給了空集合相同，皆是未說明。
    """
    out: list[str] = [f"actor_basis({basis})." for basis in sorted(set(actor_bases or ()))]
    for code, given in (measurements or {}).items():
        for m in given:
            if m.method is not None:
                out.append(f"fmethod({q(code)},{q(m.method)}).")
            if m.averaging is not None:
                out.append(f"favg({q(code)},{q(m.averaging)}).")
    for (permit, code), amounts in (permits or {}).items():
        for amount in amounts:
            out.append(f"permit_in({q(permit)},{q(code)},{milli(amount)}).")
    for code, value in case.items():
        if code in _ACCRUAL or code in _AMOUNTS:
            continue
        values = [value] if isinstance(value, int | float | str) else list(value)
        for v in values:
            if isinstance(v, str):
                out.append(f"cfact_in({q(code)},{q(v)}).")
            else:
                out.append(f"fact_in({q(code)},{milli(v)}).")
    return "\n".join(out)


# 裁處權時效的起算日；值為 YYYYMMDD，不走千分位。刑事程序以不起訴等確定之日（第 26 條第 2 項）
# 也是起算日（第 27 條第 3 項），同時表示刑事程序已確定。
_ACCRUAL = ("ap_conduct_ended", "ap_result_on", "ap_criminal_cleared")


def limitation(as_of: date, case: Case) -> str:
    """裁處日與三年期間末日；起算日不明或無效則不給末日（推論程式不標已逾）。

    始日不算入，末日為三年後相當日的前一日，無相當日則為該月末日（民法第 120 條第 2 項、
    第 121 條）；休息日不展延。只有結果日而無行為終了日，不算起算日確定。刑事程序以不起訴等
    確定者，自確定日起算（第 27 條第 3 項），並寫出 `criminal_cleared`。
    """
    lines = [f"decision({_stamp(as_of)})."]
    parsed: dict[str, date] = {}
    for code in _ACCRUAL:
        if code not in case:
            continue
        raw = case[code]
        day = None if isinstance(raw, str | Sequence) else _calendar(raw)
        if day is None:
            lines.append(f"problem(bad_date,{q(code)}).")
        else:
            parsed[code] = day
    accrual = _accrual(parsed)
    if "ap_criminal_cleared" in parsed:
        lines.append("criminal_cleared.")
        accrual = parsed["ap_criminal_cleared"]
    if accrual is not None:
        lines.append(f"deadline({_stamp(_period_end(accrual))}).")
    return "\n".join(lines)


def _stamp(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def _calendar(value: float) -> date | None:
    number = int(value)
    if number != value:
        return None
    year, month_day = divmod(number, 10000)
    month, day = divmod(month_day, 100)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _accrual(parsed: Mapping[str, date]) -> date | None:
    conduct = parsed.get("ap_conduct_ended")
    if conduct is None:
        return None
    result = parsed.get("ap_result_on")
    if result is not None and result > conduct:
        return result
    return conduct


def _period_end(accrual: date) -> date:
    start = accrual + timedelta(days=1)
    try:
        corresponding = start.replace(year=start.year + 3)
    except ValueError:
        return date(start.year + 3, 2, 28)
    return corresponding - timedelta(days=1)
