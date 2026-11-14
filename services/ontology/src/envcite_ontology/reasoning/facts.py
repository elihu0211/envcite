"""從資料庫產生推論程式的輸入事實。

時點錨定、違憲失效與授權解析在 SQL（`annotation.statement_versions_at`、
`annotation.invalidated_statements_at`、`annotation.threshold_source_at`、`annotation.delegations_at`）；
本模組只把結果寫成已解析的 ASP 事實，不重算。命令的授權鏈在這裡沿 `delegations_at` 往上走到法律。

數值一律為千分位整數（clingo 無浮點數，`numeric(18,3)` 乘一千不損失）；字串以引號輸出。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch, fetch_one, scalar
from envcite_ontology.reasoning import units

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


def q(text: str) -> str:
    """ASP 字串常數。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def milli(value: Decimal | float | int) -> int:
    """數值轉成千分位整數；先轉成 `Decimal`，不帶入浮點誤差。"""
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
    SELECT id, statement_id, condition_role, disjunct_no, concept_code,
           comparator, negated, needs_assessment, threshold, delegated,
           value_code, permit_code
      FROM annotation.conditions_of(%(ids)s::bigint[])
     WHERE condition_role <> 'limit'
     ORDER BY id
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

# 多維適用條件之所有已確認限值候選（取代只取 min(condition_id) 單一列）。
_LIMITS = """
    SELECT threshold, statement_id, measurement_method, averaging_period
      FROM annotation.threshold_sources_at(%s, %s, %s)
"""

# 跨法受控詞彙上位／等同關係（vocab.concept_relation）。
_CONCEPT_RELATIONS = """
    SELECT src.code, tgt.code, r.relation_kind::text
      FROM vocab.concept_relation AS r
      JOIN vocab.concept AS src ON src.id = r.source_concept_id
      JOIN vocab.concept AS tgt ON tgt.id = r.target_concept_id
     ORDER BY src.code, tgt.code
"""

# 準用關係（annotation.mutatis_mutandis）。
_MUTATIS = """
    SELECT source_statement_id, target_statement_id
      FROM annotation.mutatis_mutandis
     WHERE source_statement_id = ANY(%(ids)s) AND target_statement_id = ANY(%(ids)s)
     ORDER BY source_statement_id, target_statement_id
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
    SELECT statement_id, coalesce(concept_code, literal)
      FROM annotation.slots_of(%(ids)s::bigint[])
     WHERE slot_role = 'action'
"""

# 主體槽位的責任基礎：每個擇一的值一列；沒有標註的值（NULL）使主體不受責任基礎限制。
_SUBJECTS = """
    SELECT statement_id, liability_basis
      FROM annotation.slots_of(%(ids)s::bigint[])
     WHERE slot_role = 'subject'
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
    SELECT statement_id, punished_statement_id, legal_effect, min_amount, max_amount,
           per_occasion
      FROM annotation.sanctions_of(%(ids)s::bigint[])
     WHERE punished_statement_id = ANY(%(ids)s)
"""

# 被觸發者不論前提是否在當日的陳述裡都標出：前提不在（未確認、文字已改、法規未施行）就不列。
_TRIGGERS = """
    SELECT statement_id, trigger_statement_id, trigger_kind::text,
           trigger_statement_id = ANY(%(ids)s)
      FROM annotation.sanction_trigger
     WHERE statement_id = ANY(%(ids)s)
"""


class _ResourceRow(NamedTuple):
    jurisdiction: str
    id_scheme: str
    local_id: str


class _StatementRow(NamedTuple):
    id: int
    kind: str
    modality: str | None
    version_iri: str
    law_id: int


class _LawRow(NamedTuple):
    id: int
    local_id: str
    norm_kind: str


class _LinkRow(NamedTuple):
    cited_law_name: str
    cited_path: str
    # 引不到法規（或條文）時以下各欄為 NULL：LEFT JOIN 與 `delegations_at` 的外連接
    law_id: int | None
    law_local_id: str | None
    law_provision_id: int | None
    law_status: str | None
    law_norm_kind: str | None
    version_iri: str | None
    version_invalidated: bool
    law_jurisdiction: str | None
    law_id_scheme: str | None


class _ConditionRow(NamedTuple):
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


class _DefinitionRow(NamedTuple):
    law_id: int
    term: str
    member: str
    definition_id: int


class _MatchRow(NamedTuple):
    other: str
    used: str


class _ConceptRelRow(NamedTuple):
    source: str
    target: str
    kind: str


class _MutatisRow(NamedTuple):
    source: int
    target: int


class _ActionRow(NamedTuple):
    statement_id: int
    action: str


class _SubjectRow(NamedTuple):
    statement_id: int
    basis: str | None


class _ExceptionRow(NamedTuple):
    exception: int
    excluded: int


class _OverrideRow(NamedTuple):
    winner: int
    loser: int
    basis: str


class _SanctionRow(NamedTuple):
    statement_id: int
    punishes: int
    effect: str
    min_amount: Decimal | None
    max_amount: Decimal | None
    per_occasion: bool


class _TriggerRow(NamedTuple):
    statement_id: int
    trigger: int
    kind: str
    present: bool


@dataclass(frozen=True)
class Invalidated:
    """因違憲失效而不參與推論的陳述：所讀的版本、裁判字號、失效日、是否只宣告一部分。

    欄位次序同 `_INVALIDATED`，它直接是該查詢的列型別。
    """

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
    command = fetch_one(
        conn,
        "SELECT jurisdiction, id_scheme, local_id FROM corpus.legal_resource WHERE id = %s",
        (law,),
        _ResourceRow,
    )
    assert command is not None
    rows = fetch(conn, _LINKS, {"law": law, "lang": language, "on": on}, _LinkRow)
    if not rows:
        return Chain(
            False,
            (
                Link(
                    command_jurisdiction=command.jurisdiction,
                    command_id_scheme=command.id_scheme,
                    command_local_id=command.local_id,
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
    for row in rows:
        here = Link(
            command_jurisdiction=command.jurisdiction,
            command_id_scheme=command.id_scheme,
            command_local_id=command.local_id,
            cited_law_name=row.cited_law_name,
            cited_path=row.cited_path,
            target_jurisdiction=row.law_jurisdiction,
            target_id_scheme=row.law_id_scheme,
            target_local_id=row.law_local_id,
            version_iri=row.version_iri,
            gap=None,
        )
        if row.law_id is None or row.law_provision_id is None:
            links.append(_gap(here, "unresolved_law"))
        elif row.law_status != "in_force":
            links.append(_gap(here, "law_not_in_force"))
        elif row.version_invalidated:
            links.append(_gap(here, "law_invalidated"))
        elif row.law_norm_kind in LAWS:
            links.append(here)
            rooted = True
        elif row.law_id in path or row.law_id == law:
            links.append(_gap(here, "cycle"))
        else:
            links.append(here)
            above = _chain(conn, row.law_id, language, on, (*path, law))
            links.extend(above.links)
            rooted = rooted or above.rooted
    return Chain(rooted, tuple(links))


def _gap(link: Link, gap: str) -> Link:
    return replace(link, version_iri=None, gap=gap)


@dataclass(frozen=True)
class Limit:
    """委由授權條文的要件在某日所取得的限值：門檻值、提供它的陳述與它的認定方式（概念代碼）。

    欄位次序同 `_LIMIT`，它直接是該查詢的列型別。
    """

    threshold: Decimal
    statement_id: int
    method: str | None
    averaging: str | None


def limit_at(conn: Conn, condition_id: int, language: str, on: date) -> Limit | None:
    """授權的限值；解析不到為 None。同一條文對同一概念有數個限值時取要件編號最小的。"""
    return fetch_one(conn, _LIMIT, (condition_id, language, on), Limit)


def statement_facts(
    conn: Conn, as_of: date, language: str = "zho", *, include_unsettled: bool = False
) -> StatementFacts:
    """某日有效、已確認（或規則推導）的陳述及其要件、但書、優先、罰則，寫成 ASP 事實。

    違憲失效的陳述不在其中，另列在 `invalidated`；命令陳述的授權鏈不完整者寫成 `unrooted`。
    `include_unsettled` 僅供驗證閘門（接受前的可執行性檢查）納入未接受的陳述，此時不讀失效與授權。
    """
    params = {"lang": language, "on": as_of}
    resolver = "anchored_statements_at" if include_unsettled else "statement_versions_at"
    statements = fetch(conn, _STATEMENTS.format(resolver=resolver), params, _StatementRow)
    out = _statement_lines(statements)
    invalidated: tuple[Invalidated, ...] = ()
    chains: dict[int, Chain] = {}
    if not include_unsettled:
        invalidated = tuple(fetch(conn, _INVALIDATED, params, Invalidated))
        chains = _command_chains(conn, statements, language, as_of)
        out += [
            f"unrooted({stmt.id})."
            for stmt in statements
            if stmt.id in chains and not chains[stmt.id].rooted
        ]
    if not statements:
        return StatementFacts("\n".join(out), invalidated, chains)
    ids_param = {"ids": [stmt.id for stmt in statements]}

    out += _condition_facts(conn, ids_param, language, as_of)
    out += _vocabulary_facts(conn, params, ids_param)
    out += _slot_facts(conn, ids_param)
    out += _relation_facts(conn, ids_param)
    sanctions = fetch(conn, _SANCTIONS, ids_param, _SanctionRow)
    out += _sanction_lines(sanctions)
    out += _trigger_facts(conn, ids_param)
    caps = {
        sanc.statement_id: int(sanc.max_amount)
        for sanc in sanctions
        if sanc.effect == "fine" and sanc.max_amount is not None
    }
    return StatementFacts("\n".join(out), invalidated, chains, caps)


def _statement_lines(statements: Sequence[_StatementRow]) -> list[str]:
    out: list[str] = []
    for stmt in statements:
        out.append(f"statement({stmt.id},{stmt.kind},{stmt.modality or 'none'}).")
        out.append(f"src({stmt.id},{q(stmt.version_iri)}).")
        out.append(f"stmt_law({stmt.id},{stmt.law_id}).")
    return out


def _command_chains(
    conn: Conn, statements: Sequence[_StatementRow], language: str, on: date
) -> dict[int, Chain]:
    """命令陳述 → 其所屬法規的授權鏈；法律、憲法的陳述不在其中。"""
    laws = {stmt.law_id for stmt in statements}
    commands = [
        row.id for row in fetch(conn, _LAWS, (sorted(laws),), _LawRow) if row.norm_kind not in LAWS
    ]
    by_law = {law: chain_at(conn, law, language, on) for law in commands}
    return {stmt.id: by_law[stmt.law_id] for stmt in statements if stmt.law_id in by_law}


def _condition_facts(
    conn: Conn, ids_param: Mapping[str, list[int]], language: str, on: date
) -> list[str]:
    out: list[str] = []
    for cond in fetch(conn, _CONDITIONS, ids_param, _ConditionRow):
        out.append(
            f"cond({cond.id},{cond.statement_id},{cond.role},{cond.disjunct},{q(cond.concept)},"
            f"{cond.comparator},{int(cond.negated)})."
        )
        if cond.needs_assessment:
            out.append(f"judgement({cond.id}).")
        out += _condition_value_facts(conn, cond, language, on)
    return out


def _condition_value_facts(conn: Conn, cond: _ConditionRow, language: str, on: date) -> list[str]:
    """要件的比較值：概念值、門檻、許可所載的量值或委由授權條文的限值，擇一。"""
    if cond.value_concept is not None:
        # 比較子只有 eq、ne（資料庫約束）
        return [f"vcond({cond.id},{q(cond.value_concept)})."]
    if cond.threshold is not None:
        return [f"thr({cond.id},{milli(cond.threshold)})."]
    if cond.permit is not None:
        return [f"permit_cond({cond.id},{q(cond.permit)})."]  # 量值由呼叫端隨案件事實提供
    if cond.delegated:
        return _delegated_facts(conn, cond.id, language, on)
    return []


def _delegated_facts(conn: Conn, condition_id: int, language: str, on: date) -> list[str]:
    out = [f"delegated({condition_id})."]
    limits = fetch(conn, _LIMITS, (condition_id, language, on), Limit)
    if not limits:  # 解析不到則不給 thr，推論程式列為問題而非缺事實
        if _void_limit(conn, condition_id, language, on):
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


def _vocabulary_facts(
    conn: Conn, params: Mapping[str, object], ids_param: Mapping[str, list[int]]
) -> list[str]:
    """用語定義的成員、等同的概念與跨法受控詞彙階層關係。"""
    out: list[str] = []
    # 用語定義只及於本法：成員關係帶法規編號，推論只在要件所屬的法規內使用。
    definitions = fetch(conn, _DEFINITIONS, params, _DefinitionRow)
    for defn in definitions:
        out.append(f"defmember({defn.law_id},{q(defn.term)},{q(defn.member)}).")
        out.append(f"defsrc({defn.law_id},{q(defn.term)},{q(defn.member)},{defn.definition_id}).")
    definition_ids = sorted({defn.definition_id for defn in definitions})
    matches = fetch(conn, _MATCHES, {**ids_param, "definitions": definition_ids}, _MatchRow)
    out += [f"same({q(match.other)},{q(match.used)})." for match in matches]
    for rel in fetch(conn, _CONCEPT_RELATIONS, (), _ConceptRelRow):
        pred = "same" if rel.kind == "exact_match" else "concept_broader"
        out.append(f"{pred}({q(rel.source)},{q(rel.target)}).")
    return out


def _slot_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    """行為與主體槽位。"""
    out = [
        f"action({act.statement_id},{q(act.action)})."
        for act in fetch(conn, _ACTIONS, ids_param, _ActionRow)
    ]
    for subj in fetch(conn, _SUBJECTS, ids_param, _SubjectRow):
        out.append(
            f"subject_basis({subj.statement_id},{subj.basis})."
            if subj.basis is not None
            else f"subject_any({subj.statement_id})."
        )
    return out


def _relation_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    """陳述之間的但書與優先關係。"""
    out = [
        f"exception({exc.exception},{exc.excluded})."
        for exc in fetch(conn, _EXCEPTIONS, ids_param, _ExceptionRow)
    ]
    out += [
        f"override({ovr.winner},{ovr.loser},{ovr.basis})."
        for ovr in fetch(conn, _OVERRIDES, ids_param, _OverrideRow)
    ]
    out += [
        f"mutatis_mutandis({m.source},{m.target})."
        for m in fetch(conn, _MUTATIS, ids_param, _MutatisRow)
    ]
    return out


def _sanction_lines(sanctions: Sequence[_SanctionRow]) -> list[str]:
    out: list[str] = []
    for sanc in sanctions:
        lo = "none" if sanc.min_amount is None else str(int(sanc.min_amount))
        hi = "none" if sanc.max_amount is None else str(int(sanc.max_amount))
        out.append(f"sanction({sanc.statement_id},{sanc.punishes},{sanc.effect},{lo},{hi}).")
        if sanc.per_occasion:
            out.append(f"repeating({sanc.statement_id}).")
    return out


def _trigger_facts(conn: Conn, ids_param: Mapping[str, list[int]]) -> list[str]:
    out: list[str] = []
    for trig in fetch(conn, _TRIGGERS, ids_param, _TriggerRow):
        out.append(f"triggered({trig.statement_id}).")
        if trig.present:
            out.append(f"trigger({trig.statement_id},{trig.trigger},{trig.kind}).")
    return out


def _void_limit(conn: Conn, condition_id: int, language: str, on: date) -> bool:
    return scalar(conn, _VOID_LIMIT, (language, on, condition_id), bool)


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
