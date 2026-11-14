"""請求轉成推論程式的輸入：案件事實、認定方式、許可量值、行為人的責任基礎、時點。

欄位錯誤記在 `errors.Validator`，不丟例外。
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from envcite.ontology.v2 import reasoning_pb2 as pb
from envcite_ontology import errors
from envcite_ontology.reasoning.facts.case_inputs import Measurement, PerOccasionTimeline
from envcite_ontology.reasoning_codec.enums import LIABILITY_BASIS

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
    "criminal_offense",
    "court_confiscated",
    "fact_mistake",
    "ignorance_unavoidable",
    "unexpectable",
    "petty_minor",
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
    "criminal_offense",
    "court_confiscated",
    "fact_mistake",
    "ignorance_unavoidable",
    "unexpectable",
    "petty_minor",
)


_DecimalFact = Literal[
    "criminal_payment", "community_service_hours", "minimum_hourly_wage", "gained_benefit"
]


_DECIMAL_FACTS: tuple[_DecimalFact, ...] = (
    "criminal_payment",
    "community_service_hours",
    "minimum_hourly_wage",
    "gained_benefit",
)


def _stamp(day: date) -> float:
    return float(day.year * 10000 + day.month * 100 + day.day)


def finite_number(value: pb.Decimal) -> Decimal | None:
    """有限的十進位數；不是則 None。"""
    try:
        number = Decimal(value.value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def measurements(request: pb.EvaluateRequest) -> dict[str, list[Measurement]]:
    """事實的認定方式；兩者都沒給的事實不列（未提供）。"""
    out: dict[str, list[Measurement]] = {}
    for fact in request.facts:
        if fact.concept and (fact.measurement_method or fact.averaging_period):
            out.setdefault(fact.concept, []).append(
                Measurement(fact.measurement_method or None, fact.averaging_period or None)
            )
    return out


def permit_limits(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> dict[tuple[str, str], list[float]]:
    """許可所載的量值，（許可種類，概念）→ 值。欄位錯誤記在 `validator`。"""
    out: dict[tuple[str, str], list[float]] = {}
    for i, limit in enumerate(request.permit_limits):
        where = f"permit_limits[{i}]"
        if not limit.permit:
            validator.add(f"{where}.permit", errors.EMPTY, "permit is required")
        if not limit.concept:
            validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
        number = finite_number(limit.value)
        if number is None:
            validator.add(
                f"{where}.value.value", errors.INVALID_NUMBER, "must be a finite decimal number"
            )
        if limit.permit and limit.concept and number is not None:
            out.setdefault((limit.permit, limit.concept), []).append(float(number))
    return out


def actor_bases(request: pb.EvaluateRequest, validator: errors.Validator) -> list[str]:
    """行為人所屬的責任基礎，推論程式用的名稱。欄位錯誤記在 `validator`。"""
    names: dict[int, str] = {int(value): name for name, value in LIABILITY_BASIS.items()}
    out: list[str] = []
    for i, basis in enumerate(request.actor_bases):
        b_val = int(basis)
        if b_val not in names:
            validator.add(f"actor_bases[{i}]", errors.UNSET, "a liability basis is required")
        else:
            out.append(names[b_val])
    return out


def _fact_value(value: pb.FactValue, where: str, validator: errors.Validator) -> float | str | None:
    """事實的一個值；不合則記在 `validator` 並回 None。"""
    kind = value.WhichOneof("value")
    if kind == "flag":
        return 1.0 if value.flag else 0.0
    if kind == "concept":
        if not value.concept:
            validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
            return None
        return str(value.concept)
    if kind == "number":
        number = finite_number(value.number)
        if number is None:
            validator.add(
                f"{where}.number.value", errors.INVALID_NUMBER, "must be a finite decimal number"
            )
            return None
        return float(number)
    validator.add(where, errors.UNSET, "flag, number or concept is required")
    return None


def _fact_values(fact: pb.Fact, where: str, validator: errors.Validator) -> list[float | str]:
    """一筆事實的有效值；概念或值整體不合則記錯並回空。"""
    if not fact.concept:
        validator.add(f"{where}.concept", errors.EMPTY, "concept is required")
        return []
    if fact.concept.startswith(RESERVED_PREFIX):
        validator.add(
            f"{where}.concept",
            errors.RESERVED_CONCEPT,
            f"codes beginning {RESERVED_PREFIX!r} are reserved; use administrative_penalty_facts",
        )
        return []
    if not fact.values:
        validator.add(f"{where}.values", errors.EMPTY, "at least one value is required")
        return []
    decoded = [_fact_value(v, f"{where}.values[{j}]", validator) for j, v in enumerate(fact.values)]
    return [v for v in decoded if v is not None]


def _stated_facts(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> dict[str, list[float | str]]:
    """請求的 `facts`，概念 → 值；同一概念的多筆合併。"""
    several: dict[str, list[float | str]] = {}
    for i, fact in enumerate(request.facts):
        values = _fact_values(fact, f"facts[{i}]", validator)
        if values:
            several.setdefault(fact.concept, []).extend(values)
    return several


def _penalty_decimal(
    penalty: pb.AdministrativePenaltyFacts, name: str, validator: errors.Validator
) -> float | None:
    """行政罰事實的一個非負十進位數；不合則記在 `validator` 並回 None。"""
    where = f"administrative_penalty_facts.{name}"
    number = finite_number(getattr(penalty, name))
    if number is None:
        validator.add(f"{where}.value", errors.INVALID_NUMBER, "must be a finite decimal number")
        return None
    if number < 0:
        validator.add(where, errors.OUT_OF_RANGE, "must not be negative")
        return None
    return float(number)


def _penalty_facts(
    penalty: pb.AdministrativePenaltyFacts, validator: errors.Validator
) -> dict[str, float]:
    """行政罰法總則的事實，鍵已加保留前綴。"""
    out: dict[str, float] = {}
    for name in _BOOLEAN_FACTS:
        if penalty.HasField(name):
            out[f"{RESERVED_PREFIX}{name}"] = 1.0 if getattr(penalty, name) else 0.0
    if penalty.HasField("age"):
        if penalty.age < 0:
            validator.add(
                "administrative_penalty_facts.age", errors.OUT_OF_RANGE, "age must not be negative"
            )
        else:
            out[f"{RESERVED_PREFIX}age"] = float(penalty.age)
    for decimal_name in _DECIMAL_FACTS:
        if penalty.HasField(decimal_name):
            amount = _penalty_decimal(penalty, decimal_name, validator)
            if amount is not None:
                out[f"{RESERVED_PREFIX}{decimal_name}"] = amount
    return out


def case_facts(
    request: pb.EvaluateRequest,
    validator: errors.Validator,
    conduct_ended: date | None,
    result_on: date | None,
    criminal_cleared: date | None,
) -> dict[str, float | list[float | str]]:
    """請求的案件事實轉成推論程式的輸入。欄位錯誤記在 `validator`，不丟例外。"""
    case: dict[str, float | list[float | str]] = dict(_stated_facts(request, validator))
    case.update(_penalty_facts(request.administrative_penalty_facts, validator))
    for name, day in (
        ("criminal_cleared", criminal_cleared),
        ("conduct_ended", conduct_ended),
        ("result_on", result_on),
    ):
        if day is not None:
            case[f"{RESERVED_PREFIX}{name}"] = _stamp(day)
    return case


def fact_units(request: pb.EvaluateRequest) -> dict[str, str]:
    """萃取各事實所帶之量綱單位代碼。"""
    return {
        str(fact.concept): str(fact.unit)
        for fact in request.facts
        if fact.concept and getattr(fact, "unit", "")
    }


def act_groups(request: pb.EvaluateRequest) -> dict[str, list[int]]:
    """萃取行為分組（§24/§25）。"""
    return {
        str(g.act_id): [int(sid) for sid in g.statement_ids]
        for g in getattr(request, "act_groups", ())
        if g.act_id and g.statement_ids
    }


def timeline(
    request: pb.EvaluateRequest, validator: errors.Validator
) -> PerOccasionTimeline | None:
    """萃取限期改善與複查時點（釋字第 604 號）。"""
    if not hasattr(request, "HasField") or not request.HasField("timeline"):
        return None
    tl = request.timeline

    def _opt_date(raw: str, field: str) -> date | None:
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            validator.add(f"timeline.{field}", errors.INVALID_DATE, "must be ISO 8601 date")
            return None

    return PerOccasionTimeline(
        cure_notice_served_on=_opt_date(tl.cure_notice_served_on, "cure_notice_served_on"),
        cure_deadline=_opt_date(tl.cure_deadline, "cure_deadline"),
        extension_deadline=_opt_date(tl.extension_deadline, "extension_deadline"),
        reinspection_on=_opt_date(tl.reinspection_on, "reinspection_on"),
    )
