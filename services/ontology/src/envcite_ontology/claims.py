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
from typing import Any, cast

import psycopg
from psycopg.types.range import Range

from envcite_ontology.corpus_ref import UnknownProvision, provision_id
from envcite_ontology.db import Conn

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


def load(conn: Conn, doc: Mapping[str, Any], *, held_penalty: int | None = None) -> Loaded:
    """寫入一份標註文件；整份成功或整份不寫入。

    `held_penalty` 是這份文件所寫成的待連結罰則：寫入的罰則同一個交易記為它的連結。
    """
    try:
        loaded = _load(conn, doc)
        if held_penalty is not None:
            for s in doc["statements"]:
                if s.get("sanction") is not None and str(s["id"]) in loaded.ids:
                    conn.execute(
                        "INSERT INTO annotation.held_penalty_link (held_penalty_id, statement_id)"
                        " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (held_penalty, loaded.ids[str(s["id"])]),
                    )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    log.info("標註載入：%s", loaded)
    return loaded


def _load(conn: Conn, doc: Mapping[str, Any]) -> Loaded:
    try:
        return _write(conn, doc)
    except AnnotationError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
        # 文件來自外部，形狀不可信（例如物件寫成字串）。
        raise AnnotationError(f"文件欄位缺漏或型別不合：{e!r}") from e
    except psycopg.errors.IntegrityError as e:
        raise AnnotationError(f"違反資料庫約束：{e}") from e
    except psycopg.errors.DataError as e:
        raise AnnotationError(f"值不合欄位型別：{e}") from e


def _write(conn: Conn, doc: Mapping[str, Any]) -> Loaded:
    derivation = doc["derivation"]
    review = doc.get("review", "extracted")
    d_id = _one(
        conn,
        "INSERT INTO annotation.derivation (method, producer, producer_version)"
        " VALUES (%s, %s, %s) RETURNING id",
        derivation["method"],
        derivation["agent"],
        derivation["agent_version"],
    )
    concepts = _concepts(conn, doc.get("scheme"), doc.get("concepts", []))

    everything: Sequence[Mapping[str, Any]] = doc.get("statements", [])
    waiting = _waiting(conn, everything, review)
    statements = [s for s in everything if str(s["id"]) not in waiting]
    ids: dict[str, int] = {}
    conditions = duplicates = 0
    held: list[tuple[Mapping[str, Any], int]] = []
    for s in statements:
        sid = str(s["id"])
        if sid in ids:
            raise AnnotationError(f"陳述 id 重複：{sid}")
        ids[sid] = _statement(conn, s, d_id, review)
        _slots(conn, ids[sid], s.get("slots", {}), concepts)
        written = _conditions(conn, ids[sid], s.get("conditions", []), concepts)
        if s.get("sanction") is not None:
            held.append((s, written))  # 罰則的內容含所處罰的陳述：等那一方封存、id 確定後再封存
            continue
        kept = _seal(conn, ids[sid])
        if kept != ids[sid]:
            ids[sid], duplicates = kept, duplicates + 1
        else:
            conditions += written

    def ref(key: str) -> int:
        if key not in ids:
            raise AnnotationError(f"引用了不存在的陳述：{key}")
        return ids[key]

    exceptions = overrides = sanctions = triggers = 0
    for s, written in held:
        sid, sanction = str(s["id"]), s["sanction"]
        conn.execute(
            "INSERT INTO annotation.sanction"
            " (statement_id, punished_statement_id, legal_effect, amount_text,"
            "  min_amount, max_amount, per_occasion, discretionary)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ids[sid],
                int(sanction["punishes_id"])
                if sanction.get("punishes_id") is not None
                else ref(str(sanction["punishes"])),
                sanction["effect"],
                sanction.get("amount_text"),
                sanction.get("min"),
                sanction.get("max"),
                bool(sanction.get("repeating", False)),
                bool(sanction.get("discretionary", False)),
            ),
        )
        kept = _seal(conn, ids[sid])
        if kept != ids[sid]:
            ids[sid], duplicates = kept, duplicates + 1
        else:
            sanctions, conditions = sanctions + 1, conditions + written
    # 例外與優先是陳述之間的關係，兩方都封存、id 確定之後才寫；沿用既有主張時關係可能已在
    for s in statements:
        if s.get("exception_of") is not None:
            exceptions += conn.execute(
                "INSERT INTO annotation.statement_exception (exception_id, excluded_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (ids[str(s["id"])], ref(str(s["exception_of"]))),
            ).rowcount
    for s, _ in held:
        for t in s["sanction"].get("triggers") or []:
            triggers += conn.execute(
                "INSERT INTO annotation.sanction_trigger"
                " (statement_id, trigger_statement_id, trigger_kind)"
                " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    ids[str(s["id"])],
                    int(t["statement_id"])
                    if t.get("statement_id") is not None
                    else ref(str(t["statement"])),
                    t["kind"],
                ),
            ).rowcount
    for o in doc.get("overrides", []):
        overrides += conn.execute(
            "INSERT INTO annotation.statement_override"
            " (prevailing_id, subordinate_id, priority_basis)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (ref(str(o["winner"])), ref(str(o["loser"])), o["basis"]),
        ).rowcount
    for o in doc.get("overrides", []):
        if {str(o["winner"]), str(o["loser"])} & waiting:
            raise AnnotationError(f"優先關係涉及待連結的罰則：{o['winner']}、{o['loser']}")
    kept_back = _hold(conn, doc, [s for s in everything if str(s["id"]) in waiting], ids)
    defined, repeated = _definitions(conn, doc.get("definitions", []), concepts, d_id, review)
    return Loaded(
        len(ids),
        conditions,
        exceptions,
        overrides,
        sanctions,
        ids,
        defined,
        duplicates + repeated,
        triggers,
        kept_back,
    )


def _punished(s: Mapping[str, Any]) -> str | int | None:
    """罰則所處罰的陳述：文件裡的 id（str）、資料庫的編號（int），或沒有指明（None）。"""
    sanction = s["sanction"]
    if sanction.get("punishes_id") is not None:
        return int(sanction["punishes_id"])
    return None if sanction.get("punishes") is None else str(sanction["punishes"])


def _waiting(conn: Conn, statements: Sequence[Mapping[str, Any]], review: str) -> set[str]:
    """要存成待連結罰則的陳述 id：處罰的陳述未確認或沒有指明的罰則，加上以它們為前提或為例外者。"""
    in_doc = {str(s["id"]) for s in statements}
    waiting: set[str] = set()
    for s in statements:
        if s.get("sanction") is None:
            continue
        target = _punished(s)
        if isinstance(target, str):
            if target not in in_doc:
                raise AnnotationError(f"引用了不存在的陳述：{target}")
            confirmed = review == "confirmed"
        elif isinstance(target, int):
            row = conn.execute(
                "SELECT confirm_state = 'confirmed' FROM annotation.assertion WHERE id = %s",
                (target,),
            ).fetchone()
            confirmed = bool(row and row[0])
        else:
            confirmed = False
        if not confirmed:
            waiting.add(str(s["id"]))
    while True:
        more = {
            str(s["id"])
            for s in statements
            if str(s["id"]) not in waiting
            and (
                str(s.get("exception_of")) in waiting
                or any(
                    str(t.get("statement")) in waiting
                    for t in (s.get("sanction") or {}).get("triggers") or []
                )
            )
        }
        if not more:
            return waiting
        waiting |= more


def _hold(
    conn: Conn,
    doc: Mapping[str, Any],
    statements: Sequence[Mapping[str, Any]],
    ids: Mapping[str, int],
) -> int:
    """把待連結罰則依處罰對象分組存起來；以彼此為前提或為例外的同組。回傳存起來的陳述數。

    同一組必須處罰同一個對象；沒有指明受罰者的一組還要錨在同一個單元（經它的引用找被引條文）。
    內容相同的一組已存過就不再存。
    """
    group = {str(s["id"]): str(s["id"]) for s in statements}

    def root(k: str) -> str:
        while group[k] != k:
            k = group[k]
        return k

    for s in statements:
        linked = [str(s.get("exception_of"))] + [
            str(t.get("statement")) for t in (s.get("sanction") or {}).get("triggers") or []
        ]
        for other in linked:
            if other in group:
                group[root(other)] = root(str(s["id"]))
    for s in statements:
        for t in (s.get("sanction") or {}).get("triggers") or []:
            if t.get("statement_id") is None and str(t.get("statement")) not in group:
                raise AnnotationError(f"引用了不存在的陳述：{t.get('statement')}")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for s in statements:
        groups.setdefault(root(str(s["id"])), []).append(s)
    for members in groups.values():
        targets = {_punished(s) for s in members if s.get("sanction") is not None}
        if len(targets) != 1:
            raise AnnotationError(
                "以彼此為前提的罰則處罰不同的陳述：" + "、".join(str(s["id"]) for s in members)
            )
        (target,) = targets
        punished = ids[target] if isinstance(target, str) else target
        anchors = [anchor(conn, "待連結的罰則", s) for s in members]
        if punished is None and len(set(anchors)) != 1:
            # 被引條文由錨定單元的引用找，錨在數個單元就不知道該看誰的引用
            raise AnnotationError(
                "一組沒有指明受罰者的罰則錨在不同的單元："
                + "、".join(str(s["id"]) for s in members)
            )
        provision_id, digest = anchors[0]
        kept = [
            {
                **s,
                "sanction": {
                    k: v for k, v in s["sanction"].items() if k not in ("punishes", "punishes_id")
                },
            }
            if s.get("sanction") is not None
            else dict(s)
            for s in members
        ]
        document = {
            k: v for k, v in doc.items() if k not in ("statements", "definitions", "overrides")
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
                doc["derivation"]["agent"],
                doc["derivation"]["agent_version"],
                punished,
                json.dumps(document, ensure_ascii=False),
                hashlib.sha256(body.encode()).digest(),
            ),
        )
    return len(statements)


def _seal(conn: Conn, claim: int) -> int:
    """封存主張的內容；回傳留下的主張 id（已有內容相同的未駁回主張時是那一個）。"""
    return _one(conn, "SELECT annotation.seal(%s)", claim)


def _one(conn: Conn, sql: str, *args: object) -> int:
    row = conn.execute(sql, args).fetchone()
    assert row is not None
    return cast(int, row[0])


def _concepts(
    conn: Conn, scheme: Mapping[str, Any] | None, concepts: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    """該詞彙方案下文件與資料庫既有的全部概念，代碼 → id。"""
    if scheme is None:
        if concepts:
            raise AnnotationError("有概念但沒有 scheme")
        return {}
    # 方案可限定在一部法規（立法定義的用語只及於本法）；沒有 law 則是跨法共用。
    scope: int | None = None
    if scheme.get("law") is not None:
        row = conn.execute(
            "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
            (scheme["law"],),
        ).fetchone()
        if row is None:
            raise AnnotationError(f"語料裡沒有法規 {scheme['law']}")
        scope = cast(int, row[0])
    scheme_id = _one(
        conn,
        "INSERT INTO vocab.concept_scheme (code, title, scope_resource_id) VALUES (%s, %s, %s)"
        " ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        scheme["code"],
        scheme["title"],
        scope,
    )
    existing = conn.execute(
        "SELECT scope_resource_id FROM vocab.concept_scheme WHERE id = %s", (scheme_id,)
    ).fetchone()
    if existing is not None and existing[0] != scope:
        raise AnnotationError(f"詞彙方案「{scheme['code']}」已存在，且適用範圍與文件不同")
    for c in concepts:
        if "broader" in c:
            # 詞彙沒有上下位：「甲屬於乙」寫成用語定義（`definitions`），不寫在概念上。
            raise AnnotationError(f"概念「{c['code']}」帶了 broader；上下位請寫成用語定義")
        cid = _one(
            conn,
            "INSERT INTO vocab.concept (scheme_id, code, name) VALUES (%s, %s, %s)"
            " ON CONFLICT (scheme_id, code) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            scheme_id,
            c["code"],
            c["label"],
        )
        for alt in c.get("aliases", []):
            conn.execute(
                "INSERT INTO vocab.concept_label (concept_id, label, label_kind)"
                " VALUES (%s, %s, 'alias') ON CONFLICT DO NOTHING",
                (cid, alt),
            )
        _matches(conn, cid, c["code"], c.get("matches") or {})
    return {
        str(code): int(cid)  # type: ignore[call-overload]
        for code, cid in conn.execute(
            "SELECT code, id FROM vocab.concept WHERE scheme_id = %s", (scheme_id,)
        )
    }


def _matches(conn: Conn, concept_id: int, code: object, matches: Mapping[str, Any]) -> None:
    """外部對映（外部體系 → 識別碼），只記等同；與既有的對映不同就拒絕，不覆寫。"""
    for system, value in matches.items():
        external = str(value)
        conn.execute(
            "INSERT INTO vocab.concept_match (concept_id, external_system, external_id)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (concept_id, system, external),
        )
        row = conn.execute(
            "SELECT external_id FROM vocab.concept_match"
            " WHERE concept_id = %s AND external_system = %s",
            (concept_id, system),
        ).fetchone()
        if row is None or row[0] != external:
            known = None if row is None else row[0]
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


def anchor(conn: Conn, what: str, s: Mapping[str, Any]) -> tuple[int, bytes]:
    """（單元，內容雜湊）：附的原文雜湊必須是該單元現行某版本列的雜湊。"""
    provision_id = _provision(conn, s["law"], s["path"])
    digest = hashlib.sha256(str(s["text"]).encode()).digest()
    current = conn.execute(
        "SELECT 1 FROM corpus.provision_version"
        " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL",
        (provision_id, digest),
    ).fetchone()
    if current is None:
        raise AnnotationError(
            f"{what} {s['id']} 錨定的文字不是 {s['law']} {s['path']} 現行任何版本的文字"
        )
    return provision_id, digest


def _definitions(
    conn: Conn,
    definitions: Sequence[Mapping[str, Any]],
    concepts: Mapping[str, int],
    derivation_id: int,
    review: str,
) -> tuple[int, int]:
    """用語定義：錨在定義條文上；成員概念是該定義明文涵蓋的用語。回傳（定義數，沿用既有者數）。"""
    seen: set[str] = set()
    repeated = 0
    for t in definitions:
        ident = str(t["id"])
        if ident in seen:
            raise AnnotationError(f"用語定義 id 重複：{ident}")
        seen.add(ident)
        provision_id, digest = anchor(conn, "用語定義", t)
        span = t.get("span")
        did = _claim(conn, "definition", derivation_id, review)
        conn.execute(
            "INSERT INTO annotation.term_definition"
            " (id, provision_id, content_sha256, span, term_concept_id, definition_text)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                did,
                provision_id,
                digest,
                Range(span[0], span[1]) if span else None,
                _concept(concepts, t["term"]),
                t["definition"],
            ),
        )
        for member in t.get("members", []):
            conn.execute(
                "INSERT INTO annotation.term_definition_member (definition_id, member_concept_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (did, _concept(concepts, member)),
            )
        if _seal(conn, did) != did:
            repeated += 1
    return len(seen), repeated


def _statement(conn: Conn, s: Mapping[str, Any], derivation_id: int, review: str) -> int:
    provision_id, digest = anchor(conn, "陳述", s)
    span = s.get("span")
    sid = _claim(conn, "statement", derivation_id, review)
    conn.execute(
        "INSERT INTO annotation.statement"
        " (id, provision_id, content_sha256, span, statement_kind, norm_modality)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            sid,
            provision_id,
            digest,
            Range(span[0], span[1]) if span else None,
            s["kind"],
            s.get("deontic"),
        ),
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
    slots: Mapping[str, Mapping[str, Any] | Sequence[Mapping[str, Any]]],
    concepts: Mapping[str, int],
) -> None:
    """一個角色一個值，或一串彼此擇一的值。"""
    for slot, value in slots.items():
        for alternative, v in enumerate([value] if isinstance(value, Mapping) else value, 1):
            _slot(conn, statement_id, slot, alternative, v, concepts)


def _slot(
    conn: Conn,
    statement_id: int,
    slot: str,
    alternative: int,
    v: Mapping[str, Any],
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
            _concept(concepts, v["concept"]) if v.get("concept") is not None else None,
            v.get("literal"),
            v.get("liability"),
            v.get("unstated") is True,
        ),
    )


def _conditions(
    conn: Conn,
    statement_id: int,
    conditions: Sequence[Mapping[str, Any]],
    concepts: Mapping[str, int],
) -> int:
    for c in conditions:
        delegate = c.get("delegate")
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
                c["role"],
                c["clause"],
                _concept(concepts, c["concept"]),
                c["comparator"],
                c.get("threshold"),
                c.get("unit"),
                _concept(concepts, c["value_concept"]) if c.get("value_concept") else None,
                bool(c.get("negated", False)),
                _provision(conn, delegate["law"], delegate["path"]) if delegate else None,
                bool(c.get("judgement", False)),
                *(
                    _concept(concepts, c[key]) if c.get(key) else None
                    for key in ("permit", "method", "averaging")
                ),
                c.get("carried") is True,
            ),
        )
    return len(conditions)
