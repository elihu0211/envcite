"""從資料庫產生推論程式的輸入事實。

時點錨定、違憲失效與授權解析在 SQL（`annotation.statement_versions_at`、
`annotation.invalidated_statements_at`、`annotation.threshold_source_at`、`annotation.delegations_at`）；
本模組只把結果寫成已解析的 ASP 事實，不重算。命令的授權鏈在這裡沿 `delegations_at` 往上走到法律。

數值一律為千分位整數（clingo 無浮點數，`numeric(18,3)` 乘一千不損失）；字串以引號輸出。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
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

# 該日因違憲失效而被排除的陳述；與 `_STATEMENTS` 同樣只對有陳述的法規解析。
_INVALIDATED = """
    SELECT i.statement_id, corpus.version_iri(i.version_id), i.ruling_no, i.invalid_from, i.partial
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.statement AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.invalidated_statements_at(lr.id, %(lang)s, %(on)s)
                         OFFSET 0) AS i
     ORDER BY 1, 4, 3
"""

# 委由授權條文的限值，條文版本在該日已違憲失效（threshold_source_at 因而解析不到）。
_VOID_LIMIT = """
    SELECT EXISTS (
        SELECT 1 FROM annotation.statement_condition AS c
          JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
          JOIN corpus.invalidations_at(dp.resource_id, %s, %s) AS i ON i.provision_id = dp.id
         WHERE c.id = %s)
"""

_LAWS = """
    SELECT id, local_id, norm_kind::text FROM corpus.legal_resource WHERE id = ANY(%s)
"""

# 命令在該日的每一處授權：對到的法規、條文該日的狀態、位階，以及該版本是否已違憲失效。
_LINKS = """
    SELECT d.cited_law_name, d.cited_path, d.law_resource_id, d.law_local_id, d.law_provision_id,
           d.law_status::text, lr.norm_kind::text, corpus.version_iri(d.law_version_id),
           EXISTS (SELECT 1 FROM corpus.invalidations_at(d.law_resource_id, %(lang)s, %(on)s) AS i
                    WHERE i.version_id = d.law_version_id),
           lr.jurisdiction, lr.id_scheme
      FROM annotation.delegations_at(%(law)s, %(lang)s, %(on)s) AS d
      LEFT JOIN corpus.legal_resource AS lr ON lr.id = d.law_resource_id
     ORDER BY d.cited_path, d.delegation_id
"""

#: 不是被授權的命令（與 `delegation._NOT_COMMANDS` 同義）：授權鏈到此為止。
LAWS = ("constitution", "statute")

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
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.term_definition AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.definitions_at(lr.id, %(lang)s, %(on)s)
                         OFFSET 0) AS d
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

# 主體槽位的責任基礎：每個擇一的值一列；沒有標註的值（NULL）使主體不受責任基礎限制。
_SUBJECTS = """
    SELECT statement_id, liability_basis::text
      FROM annotation.statement_slot
     WHERE slot_role = 'subject' AND statement_id = ANY(%(ids)s)
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
class Invalidated:
    """因違憲失效而不參與推論的陳述：所讀的版本、裁判字號、失效日、是否只宣告一部分。"""

    statement_id: int
    version_iri: str
    ruling_no: str
    invalid_from: date
    partial: bool


@dataclass(frozen=True)
class Link:
    """授權鏈的一環：命令所引的授權條文；`gap` 為 None 即這一環成立。

    `gap`：`no_citation`、`unresolved_law`、`law_not_in_force`、`law_invalidated`、`cycle`。
    """

    command_jurisdiction: str
    command_id_scheme: str
    command_local_id: str
    cited_law_name: str
    cited_path: str
    target_jurisdiction: str | None
    target_id_scheme: str | None
    target_local_id: str | None
    version_iri: str | None
    gap: str | None


@dataclass(frozen=True)
class Chain:
    """命令的授權鏈：`rooted` 是至少一條路徑經成立的環節到達法律。"""

    rooted: bool
    links: tuple[Link, ...]


@dataclass(frozen=True)
class StatementFacts:
    """陳述寫成的 ASP 事實，與推論結果旁要附的資料。"""

    program: str
    #: 該日因違憲失效而被排除的陳述
    invalidated: tuple[Invalidated, ...] = ()
    #: 命令陳述 → 其命令的授權鏈（法律、憲法的陳述不在此）
    chains: Mapping[int, Chain] = field(default_factory=dict)
    #: 罰則陳述 → 法定最高額（罰鍰以外或沒有最高額者不在此）
    fine_caps: Mapping[int, int] = field(default_factory=dict)


def chain_at(conn: Conn, law: int, language: str, on: date) -> Chain:
    """命令在該日的授權鏈。授權者又是命令時接著往上走；回到走過的命令即 `cycle`。"""
    return _chain(conn, law, language, on, ())


def _chain(
    conn: Conn,
    law: int,
    language: str,
    on: date,
    path: tuple[int, ...],
) -> Chain:
    # 不跨起點快取：在環上被截斷的鏈只對那個起點成立。
    (cmd_jur, cmd_scheme, local_id) = cast(
        tuple[str, str, str],
        conn.execute(
            "SELECT jurisdiction, id_scheme, local_id FROM corpus.legal_resource WHERE id = %s",
            (law,),
        ).fetchone(),
    )
    rows = conn.execute(_LINKS, {"law": law, "lang": language, "on": on}).fetchall()
    if not rows:
        return Chain(
            False,
            (
                Link(
                    command_jurisdiction=str(cmd_jur),
                    command_id_scheme=str(cmd_scheme),
                    command_local_id=str(local_id),
                    cited_law_name="",
                    cited_path="",
                    target_jurisdiction=None,
                    target_id_scheme=None,
                    target_local_id=None,
                    version_iri=None,
                    gap="no_citation",
                ),
            ),
        )
    links: list[Link] = []
    rooted = False
    for (
        name,
        cited,
        parent,
        parent_id,
        provision,
        status,
        kind,
        iri,
        void,
        p_jur,
        p_scheme,
    ) in rows:
        here = Link(
            command_jurisdiction=str(cmd_jur),
            command_id_scheme=str(cmd_scheme),
            command_local_id=str(local_id),
            cited_law_name=str(name),
            cited_path=str(cited),
            target_jurisdiction=None if p_jur is None else str(p_jur),
            target_id_scheme=None if p_scheme is None else str(p_scheme),
            target_local_id=None if parent_id is None else str(parent_id),
            version_iri=None if iri is None else str(iri),
            gap=None,
        )
        if parent is None or provision is None:
            links.append(_gap(here, "unresolved_law"))
        elif status != "in_force":
            links.append(_gap(here, "law_not_in_force"))
        elif void:
            links.append(_gap(here, "law_invalidated"))
        elif kind in LAWS:
            links.append(here)
            rooted = True
        elif cast(int, parent) in path or parent == law:
            links.append(_gap(here, "cycle"))
        else:
            links.append(here)
            above = _chain(conn, cast(int, parent), language, on, (*path, law))
            links.extend(above.links)
            rooted = rooted or above.rooted
    return Chain(rooted, tuple(links))


def _gap(link: Link, gap: str) -> Link:
    return replace(link, version_iri=None, gap=gap)


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
) -> StatementFacts:
    """某日有效、已確認（或規則推導）的陳述及其要件、但書、優先、罰則，寫成 ASP 事實。

    違憲失效的陳述不在其中，另列在 `invalidated`；命令陳述的授權鏈不完整者寫成 `unrooted`。
    `include_unsettled` 僅供驗證閘門（接受前的可執行性檢查）納入未接受的陳述，此時不讀失效與授權。
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
    invalidated: tuple[Invalidated, ...] = ()
    chains: dict[int, Chain] = {}
    if not include_unsettled:
        invalidated = tuple(
            Invalidated(cast(int, sid), str(iri), str(no), cast(date, since), bool(partial))
            for sid, iri, no, since, partial in conn.execute(_INVALIDATED, params)
        )
        laws = {cast(int, row[4]) for row in statements}
        commands = [
            cast(int, law)
            for law, _, kind in conn.execute(_LAWS, (sorted(laws),))
            if kind not in LAWS
        ]
        by_law = {law: chain_at(conn, law, language, as_of) for law in commands}
        for sid, _, _, _, law_id in statements:
            chain = by_law.get(cast(int, law_id))
            if chain is not None:
                chains[cast(int, sid)] = chain
                if not chain.rooted:
                    out.append(f"unrooted({sid}).")
    if not ids:
        return StatementFacts("\n".join(out), invalidated, chains)
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
            if limit is None and _void_limit(conn, cast(int, cid), language, as_of):
                out.append(f"void_limit({cid}).")
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
    for sid, basis in conn.execute(_SUBJECTS, ids_param):
        out.append(
            f"subject_basis({sid},{basis})." if basis is not None else f"subject_any({sid})."
        )
    for exception, excluded in conn.execute(_EXCEPTIONS, ids_param):
        out.append(f"exception({exception},{excluded}).")
    for winner, loser, basis in conn.execute(_OVERRIDES, ids_param):
        out.append(f"override({winner},{loser},{basis}).")
    caps: dict[int, int] = {}
    for sid, punishes, effect, low, high, repeating in conn.execute(_SANCTIONS, ids_param):
        lo = "none" if low is None else str(int(cast(Decimal, low)))
        hi = "none" if high is None else str(int(cast(Decimal, high)))
        out.append(f"sanction({sid},{punishes},{effect},{lo},{hi}).")
        if effect == "fine" and high is not None:
            caps[cast(int, sid)] = int(cast(Decimal, high))
        if repeating:
            out.append(f"repeating({sid}).")
    for sid, trigger, kind, present in conn.execute(_TRIGGERS, ids_param):
        out.append(f"triggered({sid}).")
        if present:
            out.append(f"trigger({sid},{trigger},{kind}).")
    return StatementFacts("\n".join(out), invalidated, chains, caps)


def _void_limit(conn: Conn, condition_id: int, language: str, on: date) -> bool:
    row = conn.execute(_VOID_LIMIT, (language, on, condition_id)).fetchone()
    return bool(row and row[0])


# 裁處權時效的起算日；值為 YYYYMMDD，不走千分位。刑事程序以不起訴等確定之日（第 26 條第 2 項）
# 也是起算日（第 27 條第 3 項），同時表示刑事程序已確定。
_ACCRUAL = ("ap_conduct_ended", "ap_result_on", "ap_criminal_cleared")
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
