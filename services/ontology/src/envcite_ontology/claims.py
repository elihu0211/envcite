"""標註文件的寫入。

人寫的 JSON 由 `annotate` 這個入口讀檔後交給這裡；抽取器與罰則連結也呼叫 `load`。
只寫入與錨定，
不接受：陳述是否成為推論事實，由 `verification` 的閘門與政策決定。

**錨點是文字，不是版本列。** 陳述附原文 `text`，載入時算雜湊，且該單元現行某版本列
須有同一雜湊，否則拒絕。效力列更替而文字沒變則沿用；文字改了則自動退出該日的事實
（`annotation.statements_at`），須重新標註。

**載入不接受任何陳述。** 狀態取文件的 `review`（預設 `extracted`）；下游只取 `confirmed`
（或規則推導）者。

**罰則只處罰已確認的陳述**（綱要強制）：所處罰的陳述在文件裡未確認、在資料庫未確認，或沒有指明
（處罰的是原文引用的別條）的罰則，不寫成陳述，存成待連結罰則（`annotation.held_penalty`），連同以它為
前提或為例外者；等義務確認後由 `verification.link` 寫成陳述。

一次載入是一個推導活動、一個交易：任一處不合，整份不寫入。每個主張寫完內容即封存
（`annotation.seal`）：已有內容相同、未駁回的主張時沿用既有的，所以同一份載入兩次不會多出主張。
修正時先以 `annotation.set_confirm_state` 把舊陳述駁回，再載入新的。

文件格式：

    {"derivation": {"method": "llm", "agent": "…", "agent_version": "…"},
     "review": "confirmed",
     "scheme": {"code": "env", "title": "…", "law": "<pcode，選填：方案只及於這部法規>"},
     "concepts": [{"code": "cod", "label": "…", "aliases": ["…"], "matches": {"cas": "…"}}],
     "definitions": [{"id": "d1", "law": "<pcode>", "path": "art_2", "text": "<原文>",
                      "term": "<code>", "definition": "…", "members": ["<code>"]}],
     "statements": [
       {"id": "s1", "law": "<pcode>", "path": "art_7/para_1", "text": "<原文>",
        "kind": "prescriptive", "deontic": "obligation",
        "slots": {"action": {"concept": "discharging"},
                  "subject": [{"literal": "事業", "liability": "act"},
                              {"literal": "負責人"}]},  # 多個值彼此擇一
        "conditions": [{"role": "duty", "clause": 1, "concept": "cod", "comparator": "le",
                        "threshold": 100, "unit": "mg/L", "negated": false, "judgement": false,
                        "delegate": {"law": "<pcode>", "path": "art_2"}, "permit": "<code>",
                        "method": "<code>", "averaging": "<code>"}],
        "exception_of": "s0",
        "sanction": {"punishes": "s1", "effect": "fine", "min": 60000, "max": 20000000,
                     "repeating": false, "discretionary": false,
                     "triggers": [{"statement": "s2", "kind": "order_lapsed"}]}}],
     "overrides": [{"winner": "s2", "loser": "s1", "basis": "lex_specialis"}]}

`conditions` 的門檻值恰給一個：`threshold`、`delegate`（委由授權命令的單元）、`value_concept`，
或 `permit`（委由個別處分：許可種類的概念，量值由呼叫端隨案件事實提供）。限值（`role: limit`）
可另標認定方式：`method`（檢測方法）與 `averaging`（平均時段），都是概念代碼。
主體槽位可標責任基礎 `liability`（`act`、`state`、`representative`），其他槽位不可。
罰則 `effect` 除處罰種類外有 `order`（改善命令等非處罰的行政命令）與 `disgorgement`（不法利得的
追繳）；`repeating` 標按次處罰；`discretionary` 標條文寫「得」的效果裁量；`triggers` 是它的前提：
另一個罰則（文件裡的 `statement`，或資料庫的 `statement_id`），`kind` 為 `order_lapsed`（命令屆期
未履行，前提須是 `order`）或 `sanction_imposed`（另一處分已作成），數個前提彼此是「或」。
概念的 `matches` 是外部對映（外部體系 → 識別碼），只記等同。
`judgement` 標要件中的不確定法律概念（情節重大、必要時）：系統不判定，呼叫端給的值視為其判斷，
未給則列為需判斷；限值不得標記。效果面裁量不是要件，用罰則的 `discretionary`。
"""

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

import psycopg
from psycopg.types.range import Range
from pydantic import ValidationError

from envcite_ontology.annotation_document import (
    Concept,
    Condition,
    Definition,
    Document,
    Override,
    Scheme,
    Slot,
    Span,
    Statement,
)
from envcite_ontology.corpus_ref import UnknownProvision, provision_id
from envcite_ontology.db import Conn, fetch, fetch_one, scalar

log = logging.getLogger(__name__)


#: 文件裡可寫的列舉值，與資料庫的同名列舉一致（`tests/test_environmental_annotation.py` 守門）；
#: 抽取器的提示與解碼期 Schema 由這裡組成。
LEGAL_EFFECTS = (
    "fine",
    "confiscation",
    "restriction",
    "deprivation",
    "reputation",
    "warning",
    "order",
    "disgorgement",
)
LIABILITY_BASES = ("act", "state", "representative")
TRIGGER_KINDS = ("order_lapsed", "sanction_imposed")


class AnnotationError(ValueError):
    """文件不合：引用不存在的概念、陳述、單元，或錨定的文字不在語料裡。"""


@dataclass(frozen=True)
class Loaded:
    """一次載入實際寫入的各類列數，與文件陳述 id 對資料庫陳述編號的對照。

    例外、優先與罰則前提只計新增的列，已存在者不計。
    """

    statements: int
    conditions: int
    exceptions: int
    overrides: int
    sanctions: int
    ids: Mapping[str, int] = field(default_factory=dict)  # 文件裡的陳述 id → 資料庫的陳述編號
    definitions: int = 0
    #: 內容與既有未駁回主張相同、沿用既有主張的陳述與用語定義數
    duplicates: int = 0
    triggers: int = 0
    #: 存成待連結罰則的陳述數（不含在 statements）
    held: int = 0


def load(conn: Conn, doc: Mapping[str, object], *, held_penalty: int | None = None) -> Loaded:
    """寫入一份標註文件；整份成功或整份不寫入。

    `held_penalty` 是這份文件所寫成的待連結罰則：寫入的罰則同一個交易記為它的連結。
    """
    try:
        document = _decode(doc)
        loaded = _load(conn, doc, document)
        if held_penalty is not None:
            for s in document.statements:
                if s.sanction is not None and s.id in loaded.ids:
                    conn.execute(
                        "INSERT INTO annotation.held_penalty_link (held_penalty_id, statement_id)"
                        " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (held_penalty, loaded.ids[s.id]),
                    )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("標註載入：%s", loaded)
    return loaded


def _decode(doc: Mapping[str, object]) -> Document:
    """文件來自外部，形狀不可信（例如物件寫成字串）：寫入之前先驗證。"""
    try:
        return Document.model_validate(dict(doc))
    except ValidationError as e:
        raise AnnotationError(f"文件欄位缺漏或型別不合：{e}") from e


def _load(conn: Conn, raw: Mapping[str, object], doc: Document) -> Loaded:
    try:
        return _write(conn, raw, doc)
    except psycopg.errors.IntegrityError as e:
        raise AnnotationError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise AnnotationError(f"值不合欄位型別：{e}") from e


class _Sanctioned(NamedTuple):
    """條件已寫、等所處罰的陳述封存後才寫罰則的陳述，與它已寫的條件數。"""

    statement: Statement
    conditions: int


class _Written(NamedTuple):
    """陳述、時段與條件寫完後的狀態；`ids` 之後隨封存沿用既有主張而改動。"""

    ids: dict[str, int]
    conditions: int
    duplicates: int
    sanctioned: list[_Sanctioned]


class _SanctionsWritten(NamedTuple):
    sanctions: int
    conditions: int
    duplicates: int


def _write(conn: Conn, raw: Mapping[str, object], doc: Document) -> Loaded:
    derivation = doc.derivation
    d_id = _one(
        conn,
        "INSERT INTO annotation.derivation (method, producer, producer_version)"
        " VALUES (%s, %s, %s) RETURNING id",
        derivation.method,
        derivation.agent,
        derivation.agent_version,
    )
    concepts = _concepts(conn, doc.scheme, doc.concepts)

    waiting = _waiting(conn, doc.statements, doc.review)
    statements = [s for s in doc.statements if s.id not in waiting]
    written = _write_statements(conn, statements, concepts, d_id, doc.review)
    ids = written.ids
    sanctions = _write_sanctions(conn, written.sanctioned, ids)
    # 例外、優先與罰則前提是陳述之間的關係，兩方都封存、id 確定之後才寫；沿用既有主張時關係可能已在
    exceptions = _link_exceptions(conn, statements, ids)
    triggers = _link_triggers(conn, written.sanctioned, ids)
    overrides = _link_overrides(conn, doc.overrides, ids, waiting)
    kept_back = _hold(conn, raw, doc, [s for s in doc.statements if s.id in waiting], ids)
    defined, repeated = _definitions(conn, doc.definitions, concepts, d_id, doc.review)
    return Loaded(
        len(ids),
        written.conditions + sanctions.conditions,
        exceptions,
        overrides,
        sanctions.sanctions,
        ids,
        defined,
        written.duplicates + sanctions.duplicates + repeated,
        triggers,
        kept_back,
    )


def _ref(ids: Mapping[str, int], key: str) -> int:
    if key not in ids:
        raise AnnotationError(f"引用了不存在的陳述：{key}")
    return ids[key]


def _seal_statement(conn: Conn, ids: dict[str, int], sid: str) -> bool:
    """封存陳述的主張；已有內容相同的主張而改指既有的（`ids` 隨之更新）則回 False。"""
    kept = _seal(conn, ids[sid])
    if kept == ids[sid]:
        return True
    ids[sid] = kept
    return False


def _write_statements(
    conn: Conn,
    statements: Sequence[Statement],
    concepts: Mapping[str, int],
    derivation_id: int,
    review: str,
) -> _Written:
    """陳述連同其角色與條件；沒有罰則的當場封存，有罰則的留給 `_write_sanctions`。"""
    ids: dict[str, int] = {}
    conditions = duplicates = 0
    sanctioned: list[_Sanctioned] = []
    for s in statements:
        if s.id in ids:
            raise AnnotationError(f"陳述 id 重複：{s.id}")
        ids[s.id] = _statement(conn, s, derivation_id, review)
        _slots(conn, ids[s.id], s.slots, concepts)
        written = _conditions(conn, ids[s.id], s.conditions, concepts)
        if s.sanction is not None:
            # 罰則的內容含所處罰的陳述：等那一方封存、id 確定後再封存
            sanctioned.append(_Sanctioned(s, written))
            continue
        if _seal_statement(conn, ids, s.id):
            conditions += written
        else:
            duplicates += 1
    return _Written(ids, conditions, duplicates, sanctioned)


def _write_sanctions(
    conn: Conn, sanctioned: Sequence[_Sanctioned], ids: dict[str, int]
) -> _SanctionsWritten:
    """罰則的列，寫完即封存；`ids` 隨沿用既有主張而更新。"""
    sanctions = conditions = duplicates = 0
    for s, written in sanctioned:
        sanction = s.sanction
        assert sanction is not None  # `_write_statements` 只把有罰則的放進來
        conn.execute(
            "INSERT INTO annotation.sanction"
            " (statement_id, punished_statement_id, legal_effect, amount_text,"
            "  min_amount, max_amount, per_occasion, discretionary)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ids[s.id],
                sanction.punishes_id
                if sanction.punishes_id is not None
                else _ref(ids, str(sanction.punishes)),
                sanction.effect,
                sanction.amount_text,
                sanction.min,
                sanction.max,
                sanction.repeating,
                sanction.discretionary,
            ),
        )
        if _seal_statement(conn, ids, s.id):
            sanctions, conditions = sanctions + 1, conditions + written
        else:
            duplicates += 1
    return _SanctionsWritten(sanctions, conditions, duplicates)


def _link_exceptions(conn: Conn, statements: Sequence[Statement], ids: Mapping[str, int]) -> int:
    """新增的例外關係數。"""
    exceptions = 0
    for s in statements:
        if s.exception_of is not None:
            exceptions += conn.execute(
                "INSERT INTO annotation.statement_exception (exception_id, excluded_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (ids[s.id], _ref(ids, s.exception_of)),
            ).rowcount
    return exceptions


def _link_triggers(conn: Conn, sanctioned: Sequence[_Sanctioned], ids: Mapping[str, int]) -> int:
    """新增的罰則前提數。"""
    triggers = 0
    for s, _ in sanctioned:
        assert s.sanction is not None
        for t in s.sanction.triggers or []:
            triggers += conn.execute(
                "INSERT INTO annotation.sanction_trigger"
                " (statement_id, trigger_statement_id, trigger_kind)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    ids[s.id],
                    t.statement_id if t.statement_id is not None else _ref(ids, str(t.statement)),
                    t.kind,
                ),
            ).rowcount
    return triggers


def _link_overrides(
    conn: Conn, overrides: Sequence[Override], ids: Mapping[str, int], waiting: set[str]
) -> int:
    """新增的優先關係數；涉及待連結罰則的優先關係拒絕。"""
    written = 0
    for o in overrides:
        written += conn.execute(
            "INSERT INTO annotation.statement_override"
            " (prevailing_id, subordinate_id, priority_basis)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (_ref(ids, o.winner), _ref(ids, o.loser), o.basis),
        ).rowcount
    for o in overrides:
        if {o.winner, o.loser} & waiting:
            raise AnnotationError(f"優先關係涉及待連結的罰則：{o.winner}、{o.loser}")
    return written


def _punished(s: Statement) -> str | int | None:
    """罰則所處罰的陳述：文件裡的 id（str）、資料庫的編號（int），或沒有指明（None）。"""
    assert s.sanction is not None
    if s.sanction.punishes_id is not None:
        return s.sanction.punishes_id
    return s.sanction.punishes


def _premises(s: Statement) -> list[str]:
    """陳述在文件裡的前提：它所例外的陳述，與罰則前提指的別的罰則。"""
    triggers = s.sanction.triggers or [] if s.sanction is not None else []
    return [x for x in [s.exception_of, *(t.statement for t in triggers)] if x is not None]


class _ConfirmedRow(NamedTuple):
    confirmed: bool


def _waiting(conn: Conn, statements: Sequence[Statement], review: str) -> set[str]:
    """要存成待連結罰則的陳述 id：處罰的陳述未確認或沒有指明的罰則，加上以它們為前提或為例外者。"""
    in_doc = {s.id for s in statements}
    waiting: set[str] = set()
    for s in statements:
        if s.sanction is None:
            continue
        target = _punished(s)
        if isinstance(target, str):
            if target not in in_doc:
                raise AnnotationError(f"引用了不存在的陳述：{target}")
            confirmed = review == "confirmed"
        elif isinstance(target, int):
            row = fetch_one(
                conn,
                "SELECT confirm_state = 'confirmed' FROM annotation.assertion WHERE id = %s",
                (target,),
                _ConfirmedRow,
            )
            confirmed = row is not None and row.confirmed
        else:
            confirmed = False
        if not confirmed:
            waiting.add(s.id)
    while True:
        more = {
            s.id
            for s in statements
            if s.id not in waiting and any(p in waiting for p in _premises(s))
        }
        if not more:
            return waiting
        waiting |= more


def _hold(
    conn: Conn,
    raw: Mapping[str, object],
    doc: Document,
    statements: Sequence[Statement],
    ids: Mapping[str, int],
) -> int:
    """把待連結罰則依處罰對象分組存起來；以彼此為前提或為例外的同組。回傳存起來的陳述數。

    同一組必須處罰同一個對象；沒有指明受罰者的一組還要錨在同一個單元（經它的引用找被引條文）。
    內容相同的一組已存過就不再存。存進資料庫的是原始文件（不是解碼後的）：指紋以它的內容為準，
    重新輸出會讓已存過的一組被當成新的。
    """
    group = {s.id: s.id for s in statements}

    def root(k: str) -> str:
        while group[k] != k:
            k = group[k]
        return k

    for s in statements:
        for other in _premises(s):
            if other in group:
                group[root(other)] = root(s.id)
    for s in statements:
        for t in s.sanction.triggers or [] if s.sanction is not None else []:
            if t.statement_id is None and str(t.statement) not in group:
                raise AnnotationError(f"引用了不存在的陳述：{t.statement}")
    groups: dict[str, list[Statement]] = {}
    for s in statements:
        groups.setdefault(root(s.id), []).append(s)
    raw_statements = {str(r["id"]): r for r in _raw_statements(raw)}
    for members in groups.values():
        targets = {_punished(s) for s in members if s.sanction is not None}
        if len(targets) != 1:
            raise AnnotationError(
                "以彼此為前提的罰則處罰不同的陳述：" + "、".join(s.id for s in members)
            )
        (target,) = targets
        punished = _ref(ids, target) if isinstance(target, str) else target
        anchors = [anchor(conn, "待連結的罰則", s.id, s.law, s.path, s.text) for s in members]
        if punished is None and len(set(anchors)) != 1:
            # 被引條文由錨定單元的引用找，錨在數個單元就不知道該看誰的引用
            raise AnnotationError(
                "一組沒有指明受罰者的罰則錨在不同的單元：" + "、".join(s.id for s in members)
            )
        provision_id, digest = anchors[0]
        kept = [_without_punished(raw_statements[s.id]) for s in members]
        document = {
            k: v for k, v in raw.items() if k not in ("statements", "definitions", "overrides")
        }
        document["statements"] = kept
        body = json.dumps(
            {"document": document, "punished": punished}, ensure_ascii=False, sort_keys=True
        )
        conn.execute(
            "INSERT INTO annotation.held_penalty"
            " (provision_id, content_sha256, producer, producer_version, punished_statement_id,"
            "  document, fingerprint)"
            " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT DO NOTHING",
            (
                provision_id,
                digest,
                doc.derivation.agent,
                doc.derivation.agent_version,
                punished,
                json.dumps(document, ensure_ascii=False),
                hashlib.sha256(body.encode()).digest(),
            ),
        )
    return len(statements)


def _raw_statements(raw: Mapping[str, object]) -> list[Mapping[str, object]]:
    """原始文件的 `statements`（已通過 `_decode`，所以一定是物件的清單）。"""
    statements = raw.get("statements", [])
    assert isinstance(statements, list)
    return statements


def _without_punished(statement: Mapping[str, object]) -> dict[str, object]:
    """待連結罰則存的陳述不帶所處罰的對象：連結時由別處決定。"""
    sanction = statement.get("sanction")
    if sanction is None:
        return dict(statement)
    assert isinstance(sanction, Mapping)
    return {
        **statement,
        "sanction": {k: v for k, v in sanction.items() if k not in ("punishes", "punishes_id")},
    }


def _seal(conn: Conn, claim: int) -> int:
    """封存主張的內容；回傳留下的主張 id（已有內容相同的未駁回主張時是那一個）。"""
    return _one(conn, "SELECT annotation.seal(%s)", claim)


def _one(conn: Conn, sql: str, *args: object) -> int:
    return scalar(conn, sql, args, int)


class _IdRow(NamedTuple):
    id: int


class _ScopeRow(NamedTuple):
    scope_resource_id: int | None


class _ConceptRow(NamedTuple):
    code: str
    id: int


def _concepts(conn: Conn, scheme: Scheme | None, concepts: Sequence[Concept]) -> dict[str, int]:
    """該詞彙方案下文件與資料庫既有的全部概念，代碼 → id。"""
    if scheme is None:
        if concepts:
            raise AnnotationError("有概念但沒有 scheme")
        return {}
    # 方案可限定在一部法規（立法定義的用語只及於本法）；沒有 law 則是跨法共用。
    scope: int | None = None
    if scheme.law is not None:
        row = fetch_one(
            conn,
            "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
            (scheme.law,),
            _IdRow,
        )
        if row is None:
            raise AnnotationError(f"語料裡沒有法規 {scheme.law}")
        scope = row.id
    scheme_id = _one(
        conn,
        "INSERT INTO vocab.concept_scheme (code, title, scope_resource_id) VALUES (%s, %s, %s)"
        " ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        scheme.code,
        scheme.title,
        scope,
    )
    existing = fetch_one(
        conn,
        "SELECT scope_resource_id FROM vocab.concept_scheme WHERE id = %s",
        (scheme_id,),
        _ScopeRow,
    )
    if existing is not None and existing.scope_resource_id != scope:
        raise AnnotationError(f"詞彙方案「{scheme.code}」已存在，且適用範圍與文件不同")
    for c in concepts:
        if "broader" in c.model_fields_set:
            # 詞彙沒有上下位：「甲屬於乙」寫成用語定義（`definitions`），不寫在概念上。
            raise AnnotationError(f"概念「{c.code}」帶了 broader；上下位請寫成用語定義")
        cid = _one(
            conn,
            "INSERT INTO vocab.concept (scheme_id, code, name) VALUES (%s, %s, %s)"
            " ON CONFLICT (scheme_id, code) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            scheme_id,
            c.code,
            c.label,
        )
        for alt in c.aliases:
            conn.execute(
                "INSERT INTO vocab.concept_label (concept_id, label, label_kind)"
                " VALUES (%s, %s, 'alias') ON CONFLICT DO NOTHING",
                (cid, alt),
            )
        _matches(conn, cid, c.code, c.matches or {})
    return {
        r.code: r.id
        for r in fetch(
            conn,
            "SELECT code, id FROM vocab.concept WHERE scheme_id = %s",
            (scheme_id,),
            _ConceptRow,
        )
    }


class _ExternalRow(NamedTuple):
    external_id: str


def _matches(conn: Conn, concept_id: int, code: object, matches: Mapping[str, str]) -> None:
    """外部對映（外部體系 → 識別碼），只記等同；與既有的對映不同就拒絕，不覆寫。"""
    for system, external in matches.items():
        conn.execute(
            "INSERT INTO vocab.concept_match (concept_id, external_system, external_id)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (concept_id, system, external),
        )
        row = fetch_one(
            conn,
            "SELECT external_id FROM vocab.concept_match"
            " WHERE concept_id = %s AND external_system = %s",
            (concept_id, system),
            _ExternalRow,
        )
        if row is None or row.external_id != external:
            known = None if row is None else row.external_id
            raise AnnotationError(f"概念「{code}」在 {system} 已對映到 {known}，不是 {external}")


def _concept(concepts: Mapping[str, int], code: object) -> int:
    if str(code) not in concepts:
        raise AnnotationError(f"引用了不存在的概念：{code}")
    return concepts[str(code)]


def _provision(conn: Conn, law: object, path: object) -> int:
    try:
        return provision_id(conn, law, path)
    except UnknownProvision as e:
        raise AnnotationError(str(e)) from e


def anchor(conn: Conn, what: str, ident: str, law: str, path: str, text: str) -> tuple[int, bytes]:
    """（單元，內容雜湊）：附的原文雜湊必須是該單元現行某版本列的雜湊。"""
    provision_id = _provision(conn, law, path)
    digest = hashlib.sha256(text.encode()).digest()
    current = conn.execute(
        "SELECT 1 FROM corpus.provision_version"
        " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL",
        (provision_id, digest),
    ).fetchone()
    if current is None:
        raise AnnotationError(f"{what} {ident} 錨定的文字不是 {law} {path} 現行任何版本的文字")
    return provision_id, digest


def _range(span: Span) -> Range[int] | None:
    return None if span is None else Range(span[0], span[1])


def _definitions(
    conn: Conn,
    definitions: Sequence[Definition],
    concepts: Mapping[str, int],
    derivation_id: int,
    review: str,
) -> tuple[int, int]:
    """用語定義：錨在定義條文上；成員概念是該定義明文涵蓋的用語。回傳（定義數，沿用既有者數）。"""
    seen: set[str] = set()
    repeated = 0
    for t in definitions:
        if t.id in seen:
            raise AnnotationError(f"用語定義 id 重複：{t.id}")
        seen.add(t.id)
        provision_id, digest = anchor(conn, "用語定義", t.id, t.law, t.path, t.text)
        did = _claim(conn, "definition", derivation_id, review)
        conn.execute(
            "INSERT INTO annotation.term_definition"
            " (id, provision_id, content_sha256, span, term_concept_id, definition_text)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (did, provision_id, digest, _range(t.span), _concept(concepts, t.term), t.definition),
        )
        for member in t.members:
            conn.execute(
                "INSERT INTO annotation.term_definition_member (definition_id, member_concept_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (did, _concept(concepts, member)),
            )
        if _seal(conn, did) != did:
            repeated += 1
    return len(seen), repeated


def _statement(conn: Conn, s: Statement, derivation_id: int, review: str) -> int:
    provision_id, digest = anchor(conn, "陳述", s.id, s.law, s.path, s.text)
    sid = _claim(conn, "statement", derivation_id, review)
    conn.execute(
        "INSERT INTO annotation.statement"
        " (id, provision_id, content_sha256, span, statement_kind, norm_modality)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (sid, provision_id, digest, _range(s.span), s.kind, s.deontic),
    )
    return sid


def _claim(conn: Conn, kind: str, derivation_id: int, review: str) -> int:
    """主張本身（來源推導與審核狀態）；子型別的列以同一個 id 寫入。"""
    return _one(
        conn,
        "INSERT INTO annotation.assertion (assertion_kind, derivation_id, confirm_state)"
        " VALUES (%s, %s, %s) RETURNING id",
        kind,
        derivation_id,
        review,
    )


def _slots(
    conn: Conn,
    statement_id: int,
    slots: Mapping[str, Slot | list[Slot]],
    concepts: Mapping[str, int],
) -> None:
    """一個角色一個值，或一串彼此擇一的值。"""
    for slot, value in slots.items():
        for alternative, v in enumerate([value] if isinstance(value, Slot) else value, 1):
            _slot(conn, statement_id, slot, alternative, v, concepts)


def _slot(
    conn: Conn,
    statement_id: int,
    slot: str,
    alternative: int,
    v: Slot,
    concepts: Mapping[str, int],
) -> None:
    conn.execute(
        "INSERT INTO annotation.statement_slot"
        " (statement_id, slot_role, alternative, concept_id, literal, liability_basis, unstated)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            statement_id,
            slot,
            alternative,
            _concept(concepts, v.concept) if v.concept is not None else None,
            v.literal,
            v.liability,
            v.unstated is True,
        ),
    )


def _conditions(
    conn: Conn, statement_id: int, conditions: Sequence[Condition], concepts: Mapping[str, int]
) -> int:
    for c in conditions:
        conn.execute(
            "INSERT INTO annotation.statement_condition"
            " (statement_id, condition_role, disjunct_no, concept_id, comparator,"
            "  threshold, measure_unit,"
            "  value_concept_id, negated, delegate_provision_id, needs_assessment,"
            "  delegate_permit_concept_id, measurement_method_concept_id,"
            "  averaging_period_concept_id, carried)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                statement_id,
                c.role,
                c.clause,
                _concept(concepts, c.concept),
                c.comparator,
                c.threshold,
                c.unit,
                _concept(concepts, c.value_concept) if c.value_concept else None,
                c.negated,
                _provision(conn, c.delegate.law, c.delegate.path) if c.delegate else None,
                c.judgement,
                *(
                    _concept(concepts, code) if code else None
                    for code in (c.permit, c.method, c.averaging)
                ),
                c.carried is True,
            ),
        )
    return len(conditions)
