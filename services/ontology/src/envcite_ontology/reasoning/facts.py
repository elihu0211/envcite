"""從資料庫產生推論程式的輸入事實。

時點錨定與授權解析在 SQL（`annotation.statement_versions_at`、`annotation.threshold_source_at`）；
本模組只把結果寫成已解析的 ASP 事實，不重算。

數值一律為千分位整數（clingo 無浮點數，`numeric(18,3)` 乘一千不損失）；字串以引號輸出。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

# 案件事實的值：數字，或概念代碼（概念取某個值概念）；同一概念可帶多個值。
CaseValue = float | str
Case = Mapping[str, CaseValue | Sequence[CaseValue]]


@dataclass(frozen=True)
class Measurement:
    """案件事實的認定方式：檢測方法與平均時段，都是概念代碼；None 是未提供。"""

    method: str | None = None
    averaging: str | None = None


#: 事實概念 → 它的值所用的認定方式（同一概念可由數筆事實提供，各帶認定方式）。
Measurements = Mapping[str, Sequence[Measurement]]
#: （許可種類，概念）→ 該場所持有的這種許可所載、這個概念的量值（委由個別處分的門檻值）。
Permits = Mapping[tuple[str, str], Sequence[float]]


def q(text: str) -> str:
    """ASP 字串常數。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def milli(value: Decimal | float | int) -> int:
    return round(Decimal(str(value)) * 1000)


_STATEMENTS = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id),
           lr.id
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.statement AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.{resolver}(lr.id, %(lang)s, %(on)s) OFFSET 0) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""
# 由陳述出發，只對「有陳述的法規」解析時點；沒有陳述的法規本來就不回任何列，結果不變
# （`provision.resource_id` 是指向 `legal_resource` 的非空外鍵，不必再連 `legal_resource`）。
# `OFFSET 0` 是最佳化屏障，不是殘留：解析函式是單一 SELECT 的 SQL 函式，會被內聯進外層查詢，
# 規劃器把「各法規的現行條文」估成每部一列，便排出對 `provision_version` 的巢狀迴圈加
# `Join Filter (v.id = a.version_id)`——線上抽樣二十部法規就比對了一億次，全庫要十幾分鐘，
# 縮到一部法規仍要數百毫秒。屏障讓函式維持「每部法規一次、以條文編號走索引」的計畫；拿掉就會退回。

_CONDITIONS = """
    SELECT c.id, c.statement_id, c.condition_role::text, c.disjunct_no, k.code,
           c.comparator::text, c.negated,
           c.needs_assessment, c.threshold, c.delegate_provision_id IS NOT NULL,
           vk.code, pk.code
      FROM annotation.statement_condition AS c
      JOIN vocab.concept AS k ON k.id = c.concept_id
      LEFT JOIN vocab.concept AS vk ON vk.id = c.value_concept_id
      LEFT JOIN vocab.concept AS pk ON pk.id = c.delegate_permit_concept_id
     WHERE c.statement_id = ANY(%(ids)s) AND c.condition_role <> 'limit'
     ORDER BY c.id
"""

_DEFINITIONS = """
    SELECT lr.id, tk.code, mk.code, t.id
      FROM corpus.legal_resource AS lr
     CROSS JOIN LATERAL annotation.definitions_at(lr.id, %(lang)s, %(on)s) AS d
      JOIN annotation.term_definition AS t ON t.id = d.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
      JOIN annotation.term_definition_member AS m ON m.definition_id = t.id
      JOIN vocab.concept AS mk ON mk.id = m.member_concept_id
     ORDER BY lr.id, tk.code, mk.code
"""

# 授權的限值與它的認定方式（限值那一列要件上的檢測方法、平均時段）。
_LIMIT = """
    SELECT s.threshold, s.statement_id, mk.code, ak.code
      FROM annotation.threshold_source_at(%s, %s, %s) AS s
      JOIN annotation.statement_condition AS lim ON lim.id = s.condition_id
      LEFT JOIN vocab.concept AS mk ON mk.id = lim.measurement_method_concept_id
      LEFT JOIN vocab.concept AS ak ON ak.id = lim.averaging_period_concept_id
     ORDER BY s.condition_id
"""

# 對到同一個外部識別碼的概念視為同一個（只記等同）：要件、概念值與定義成員所用的概念，
# 和與它等同的其他概念之代碼。推論把後者的事實當成前者的事實。
_MATCHES = """
    SELECT DISTINCT other.code, used.code
      FROM vocab.concept AS used
      JOIN vocab.concept_match AS m ON m.concept_id = used.id
      JOIN vocab.concept_match AS om ON om.external_system = m.external_system
                                    AND om.external_id = m.external_id
                                    AND om.concept_id <> m.concept_id
      JOIN vocab.concept AS other ON other.id = om.concept_id
     WHERE other.code <> used.code
       AND used.id IN (SELECT c.concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT c.value_concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT c.delegate_permit_concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT m.member_concept_id FROM annotation.term_definition_member AS m
                        WHERE m.definition_id = ANY(%(definitions)s::bigint[]))
     ORDER BY 1, 2
"""

_ACTIONS = """
    SELECT sl.statement_id, coalesce(k.code, sl.literal)
      FROM annotation.statement_slot AS sl
      LEFT JOIN vocab.concept AS k ON k.id = sl.concept_id
     WHERE sl.slot_role = 'action' AND sl.statement_id = ANY(%(ids)s)
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
    SELECT statement_id, punished_statement_id, legal_effect::text, min_amount, max_amount,
           per_occasion
      FROM annotation.sanction
     WHERE statement_id = ANY(%(ids)s) AND punished_statement_id = ANY(%(ids)s)
"""

# 被觸發者不論前提是否在當日的陳述裡都標出：前提不在（未確認、文字已改、法規未施行）就不列。
_TRIGGERS = """
    SELECT statement_id, trigger_statement_id, trigger_kind::text,
           trigger_statement_id = ANY(%(ids)s)
      FROM annotation.sanction_trigger
     WHERE statement_id = ANY(%(ids)s)
"""


@dataclass(frozen=True)
class Limit:
    """委由授權條文的要件在某日所取得的限值：門檻值、提供它的陳述與它的認定方式（概念代碼）。"""

    threshold: Decimal
    statement_id: int
    method: str | None
    averaging: str | None


def limit_at(conn: Conn, condition_id: int, language: str, on: date) -> Limit | None:
    """授權的限值；解析不到為 None。同一條文對同一概念有數個限值時取要件編號最小的。"""
    row = conn.execute(_LIMIT, (condition_id, language, on)).fetchone()
    if row is None:
        return None
    threshold, statement_id, method, averaging = row
    return Limit(
        cast(Decimal, threshold),
        cast(int, statement_id),
        None if method is None else str(method),
        None if averaging is None else str(averaging),
    )


def statement_facts(
    conn: Conn, as_of: date, language: str = "zho", *, include_unsettled: bool = False
) -> str:
    """某日有效、已確認（或規則推導）的陳述及其要件、但書、優先、罰則，寫成 ASP 事實。

    `include_unsettled` 僅供驗證閘門（接受前的可執行性檢查）納入未接受的陳述。
    """
    out: list[str] = []
    params = {"lang": language, "on": as_of}
    resolver = "anchored_statements_at" if include_unsettled else "statement_versions_at"
    statements = conn.execute(_STATEMENTS.format(resolver=resolver), params).fetchall()
    ids = [cast(int, row[0]) for row in statements]
    for sid, kind, deontic, iri, law_id in statements:
        out.append(f"statement({sid},{kind},{deontic or 'none'}).")
        out.append(f"src({sid},{q(str(iri))}).")
        out.append(f"stmt_law({sid},{law_id}).")
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
            value_concept,
            permit,
        ) = row
        out.append(
            f"cond({cid},{sid},{role},{clause},{q(str(code))},{comparator},{int(bool(negated))})."
        )
        if judgement:
            out.append(f"judgement({cid}).")
        if value_concept is not None:
            out.append(f"vcond({cid},{q(str(value_concept))}).")  # 比較子只有 eq、ne（資料庫約束）
        elif threshold is not None:
            out.append(f"thr({cid},{milli(cast(Decimal, threshold))}).")
        elif permit is not None:
            out.append(f"permit_cond({cid},{q(str(permit))}).")  # 量值由呼叫端隨案件事實提供
        elif delegated:
            out.append(f"delegated({cid}).")
            limit = limit_at(conn, cast(int, cid), language, as_of)
            if limit is not None:  # 解析不到則不給 thr，推論程式列為問題而非缺事實
                out.append(f"thr({cid},{milli(limit.threshold)}).")
                out.append(f"thr_src({cid},{limit.statement_id}).")
                if limit.method is not None:
                    out.append(f"lim_method({cid},{q(limit.method)}).")
                if limit.averaging is not None:
                    out.append(f"lim_avg({cid},{q(limit.averaging)}).")
                if limit.method is None and limit.averaging is None:
                    out.append(f"lim_unlabeled({cid}).")

    # 用語定義只及於本法：成員關係帶法規編號，推論只在要件所屬的法規內使用。
    definitions: set[int] = set()
    for law_id, term, member, definition_id in conn.execute(_DEFINITIONS, params):
        out.append(f"defmember({law_id},{q(str(term))},{q(str(member))}).")
        out.append(f"defsrc({law_id},{q(str(term))},{q(str(member))},{definition_id}).")
        definitions.add(cast(int, definition_id))
    for other, used in conn.execute(_MATCHES, ids_param | {"definitions": sorted(definitions)}):
        out.append(f"same({q(str(other))},{q(str(used))}).")
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
    for sid, trigger, kind, present in conn.execute(_TRIGGERS, ids_param):
        out.append(f"triggered({sid}).")
        if present:
            out.append(f"trigger({sid},{trigger},{kind}).")
    return "\n".join(out)


# 裁處權時效的起算日；值為 YYYYMMDD，不走千分位。
_ACCRUAL = ("ap_conduct_ended", "ap_result_on")


def case_facts(
    case: Case, measurements: Measurements | None = None, permits: Permits | None = None
) -> str:
    """案件事實：概念代碼 → 值（同一概念可有多個值，矛盾由推論程式檢出）。

    數字是千分位的 `fact_in`；字串是概念值（行業屬某行業），寫成 `cfact_in`。認定方式寫成
    `fmethod`／`favg`；許可所載的量值寫成 `permit_in`（同一許可、同一概念有兩個值即矛盾）。
    """
    out: list[str] = []
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
        if code in _ACCRUAL:
            continue
        values = [value] if isinstance(value, int | float | str) else list(value)
        for v in values:
            if isinstance(v, str):
                out.append(f"cfact_in({q(code)},{q(v)}).")
            else:
                out.append(f"fact_in({q(code)},{milli(v)}).")
    return "\n".join(out)


def limitation(as_of: date, case: Case) -> str:
    """裁處日與三年期間末日；起算日不明或無效則不給末日（推論程式不標已逾）。

    始日不算入，末日為三年後相當日的前一日，無相當日則為該月末日（民法第 120 條第 2 項、
    第 121 條）；休息日不展延。只有結果日而無行為終了日，不算起算日確定。
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
