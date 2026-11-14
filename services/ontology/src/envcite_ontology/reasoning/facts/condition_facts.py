"""要件的事實：比較值是概念值、門檻、許可所載的量值，或委由授權條文的限值（擇一）。"""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.facts.asp import milli, q
from envcite_ontology.reasoning.facts.chains import DelegatedLimitRow, batch_delegated_limits

_CONDITIONS = """
    SELECT id, statement_id, condition_role, disjunct_no, concept_code,
           comparator, negated, needs_assessment, threshold, delegated,
           value_code, permit_code
      FROM annotation.conditions_of(%(ids)s::bigint[])
     WHERE condition_role <> 'limit'
     ORDER BY id
"""


class ConditionRow(NamedTuple):
    """一個要件的列：角色、析取編號、概念、比較子，與門檻的各種來源。"""

    id: int
    statement_id: int
    role: str
    disjunct: int
    concept: str
    comparator: str
    negated: bool
    needs_assessment: bool
    threshold: Decimal | None
    delegated: bool
    value_concept: str | None
    permit: str | None


def condition_facts(
    conn: Conn, ids_param: Mapping[str, list[int]], language: str, on: date
) -> list[str]:
    """這些陳述的要件事實；委由授權條文的要件成批解析限值。"""
    conditions = fetch(conn, _CONDITIONS, ids_param, ConditionRow)
    delegated_ids = [
        cond.id
        for cond in conditions
        if cond.value_concept is None
        and cond.threshold is None
        and cond.permit is None
        and cond.delegated
    ]
    limits_by_cond, void_ids = batch_delegated_limits(conn, delegated_ids, language, on)
    out: list[str] = []
    for cond in conditions:
        out.append(
            f"cond({cond.id},{cond.statement_id},{cond.role},{cond.disjunct},{q(cond.concept)},"
            f"{cond.comparator},{int(cond.negated)})."
        )
        if cond.needs_assessment:
            out.append(f"judgement({cond.id}).")
        out += _condition_value_facts(cond, limits_by_cond, void_ids)
    return out


def _condition_value_facts(
    cond: ConditionRow,
    limits_by_cond: Mapping[int, list[DelegatedLimitRow]],
    void_ids: set[int],
) -> list[str]:
    """要件的比較值：概念值、門檻、許可所載的量值或委由授權條文的限值，擇一。"""
    if cond.value_concept is not None:
        # 比較子只有 eq、ne（資料庫約束）
        return [f"vcond({cond.id},{q(cond.value_concept)})."]
    if cond.threshold is not None:
        return [f"thr({cond.id},{milli(cond.threshold)})."]
    if cond.permit is not None:
        return [f"permit_cond({cond.id},{q(cond.permit)})."]  # 量值由呼叫端隨案件事實提供
    if cond.delegated:
        return _delegated_facts(cond.id, limits_by_cond.get(cond.id, ()), cond.id in void_ids)
    return []


def _delegated_facts(
    condition_id: int, limits: Sequence[DelegatedLimitRow], is_void: bool
) -> list[str]:
    out = [f"delegated({condition_id})."]
    if not limits:  # 解析不到則不給 thr，推論程式列為問題而非缺事實
        if is_void:
            out.append(f"void_limit({condition_id}).")
        return out
    for limit in limits:
        out.append(f"limit_cand({condition_id},{limit.statement_id},{milli(limit.threshold)}).")
        if limit.method is not None:
            out.append(f"cand_lim_method({condition_id},{limit.statement_id},{q(limit.method)}).")
        if limit.averaging is not None:
            out.append(f"cand_lim_avg({condition_id},{limit.statement_id},{q(limit.averaging)}).")
        if limit.method is None and limit.averaging is None:
            out.append(f"cand_lim_unlabeled({condition_id},{limit.statement_id}).")
    return out
