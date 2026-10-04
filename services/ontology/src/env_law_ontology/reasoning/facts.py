"""從資料庫產生推論程式的輸入事實（env-law-docs 02-05 §5.1、§5.3）。

時點錨定與授權解析留在 SQL（`annotation.statement_versions_at`、`annotation.threshold_source_at`）：
本模組只把它們的結果寫成 ASP 事實，不再用另一套連接重算。輸出的是**已解析**的事實，推論程式不知道
任何法條。

數值一律為千分位整數（clingo 沒有浮點數）：`numeric(18,3)` 乘以一千取整，三位小數內不損失。
字串（概念代碼、行為）以引號輸出，所以代碼可以是任何文字。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]


def q(text: str) -> str:
    """ASP 字串常數。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def milli(value: Decimal | float | int) -> int:
    return round(Decimal(str(value)) * 1000)


_STATEMENTS = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id)
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.{resolver}(lr.id, %(lang)s, %(on)s) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""

_CONDITIONS = """
    SELECT c.id, c.statement_id, c.cond_role::text, c.disjunct_no, k.code,
           c.comparator::text, c.negated,
           c.needs_assessment, c.threshold, c.delegate_provision_id IS NOT NULL,
           c.value_concept_id IS NOT NULL
      FROM annotation.statement_condition AS c
      JOIN vocab.concept AS k ON k.id = c.concept_id
     WHERE c.statement_id = ANY(%(ids)s) AND c.cond_role <> 'limit'
     ORDER BY c.id
"""

_SOURCES = "SELECT threshold, statement_id FROM annotation.threshold_source_at(%s, %s, %s)"

_ACTIONS = """
    SELECT sl.statement_id, coalesce(k.code, sl.literal)
      FROM annotation.statement_slot AS sl
      LEFT JOIN vocab.concept AS k ON k.id = sl.concept_id
     WHERE sl.slot = 'action' AND sl.statement_id = ANY(%(ids)s)
"""

_EXCEPTIONS = """
    SELECT exception_id, excluded_id FROM annotation.statement_exception
     WHERE exception_id = ANY(%(ids)s) AND excluded_id = ANY(%(ids)s)
"""

_OVERRIDES = """
    SELECT prevailing_id, subordinate_id, priority_basis::text FROM annotation.statement_override
     WHERE prevailing_id = ANY(%(ids)s) AND subordinate_id = ANY(%(ids)s)
"""

_SANCTIONS = """
    SELECT statement_id, punishes_id, legal_effect::text, min_amount, max_amount, per_occasion
      FROM annotation.sanction
     WHERE statement_id = ANY(%(ids)s) AND punishes_id = ANY(%(ids)s)
"""


def statement_facts(
    conn: Conn, as_of: date, language: str = "zho", *, include_unsettled: bool = False
) -> str:
    """某日有效、已確認（或規則推導）的陳述及其要件、但書、優先、罰則，寫成 ASP 事實。

    `include_unsettled` 只給驗證閘門用：連尚未被接受的陳述也納入，才能在接受之前評估它
    （可執行性檢查）。推論的輸入一律不含。
    """
    out: list[str] = []
    params = {"lang": language, "on": as_of}
    resolver = "anchored_statements_at" if include_unsettled else "statement_versions_at"
    statements = conn.execute(_STATEMENTS.format(resolver=resolver), params).fetchall()
    ids = [cast(int, row[0]) for row in statements]
    for sid, kind, deontic, iri in statements:
        out.append(f"statement({sid},{kind},{deontic or 'none'}).")
        out.append(f"src({sid},{q(str(iri))}).")
    if not ids:
        return "\n".join(out)
    ids_param = {"ids": ids}

    for row in conn.execute(_CONDITIONS, ids_param).fetchall():
        (
            cid,
            sid,
            role,
            clause,
            code,
            comparator,
            negated,
            judgement,
            threshold,
            delegated,
            by_concept,
        ) = row
        out.append(
            f"cond({cid},{sid},{role},{clause},{q(str(code))},{comparator},{int(bool(negated))})."
        )
        if judgement:
            out.append(f"judgement({cid}).")
        if by_concept:
            out.append(f"unsupported({cid}).")  # 概念值的比較：推論程式不做
        elif threshold is not None:
            out.append(f"thr({cid},{milli(cast(Decimal, threshold))}).")
        elif delegated:
            out.append(f"delegated({cid}).")
            source = conn.execute(_SOURCES, (cid, language, as_of)).fetchone()
            if source is not None:  # 解析不到就沒有 thr 事實：推論程式會列為問題，不冒充缺事實
                out.append(f"thr({cid},{milli(cast(Decimal, source[0]))}).")
                out.append(f"thr_src({cid},{source[1]}).")

    for sid, action in conn.execute(_ACTIONS, ids_param):
        out.append(f"action({sid},{q(str(action))}).")
    for exception, excluded in conn.execute(_EXCEPTIONS, ids_param):
        out.append(f"exception({exception},{excluded}).")
    for winner, loser, basis in conn.execute(_OVERRIDES, ids_param):
        out.append(f"override({winner},{loser},{basis}).")
    for sid, punishes, effect, low, high, repeating in conn.execute(_SANCTIONS, ids_param):
        lo = "none" if low is None else str(int(cast(Decimal, low)))
        hi = "none" if high is None else str(int(cast(Decimal, high)))
        out.append(f"sanction({sid},{punishes},{effect},{lo},{hi}).")
        if repeating:
            out.append(f"repeating({sid}).")
    return "\n".join(out)


# 裁處權時效的起算日。不走千分位：值是 YYYYMMDD。
_ACCRUAL = ("ap_conduct_ended", "ap_result_on")


def case_facts(case: Mapping[str, float | Sequence[float]]) -> str:
    """案件事實：概念代碼 → 值（同一概念可有多個值，矛盾由推論程式檢出）。"""
    out: list[str] = []
    for code, value in case.items():
        if code in _ACCRUAL:
            continue
        values = [value] if isinstance(value, int | float) else list(value)
        out.extend(f"fact_in({q(code)},{milli(v)})." for v in values)
    return "\n".join(out)


def limitation(as_of: date, case: Mapping[str, float | Sequence[float]]) -> str:
    """裁處日與三年期間的末日。末日由日曆算出；起算日不明或日期無效則不給末日，推論程式就不標已逾。

    始日不算入，末日是起算日三年後相當日的前一日；該月沒有相當日則以該月最後一日為末日
    （民法第 120 條第 2 項、第 121 條）。休息日不展延。
    只有結果日、沒有行為終了日，不算已確定起算日。
    """
    lines = [f"decision({_stamp(as_of)})."]
    parsed: dict[str, date] = {}
    for code in _ACCRUAL:
        if code not in case:
            continue
        raw = case[code]
        day = None if isinstance(raw, Sequence) else _calendar(raw)
        if day is None:
            lines.append(f"problem(bad_date,{q(code)}).")
        else:
            parsed[code] = day
    accrual = _accrual(parsed)
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
