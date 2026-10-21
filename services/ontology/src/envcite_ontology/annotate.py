"""標註文件的載入。

抽取器（`verification/extract.py`）產生的 JSON 文件，寫進 `annotation` 與 `vocab`。只寫入與錨定，
不接受：陳述是否成為推論事實，由 `verification` 的閘門與政策決定。

**錨點是文字，不是版本列。** 陳述附原文 `text`，載入時算雜湊，且該單元現行某版本列
須有同一雜湊，否則拒絕。效力列更替而文字沒變則沿用；文字改了則自動退出該日的事實
（`annotation.statements_at`），須重新標註。

**載入不接受任何陳述。** 狀態取文件的 `review`（預設 `extracted`）；下游只取 `confirmed`
（或規則推導）者。

一次載入是一個推導活動、一個交易：任一處不合，整份不寫入。每個主張寫完內容即封存
（`annotation.seal`）：已有內容相同、未駁回的主張時沿用既有的，所以同一份載入兩次不會多出主張。
修正時先以 `annotation.set_confirm_state` 把舊陳述駁回，再載入新的。

文件格式：

    {"derivation": {"method": "llm", "agent": "…", "agent_version": "…"},
     "review": "confirmed",
     "scheme": {"code": "env", "title": "…", "law": "<pcode，選填：方案只及於這部法規>"},
     "concepts": [{"code": "cod", "label": "…", "aliases": ["…"]}],
     "definitions": [{"id": "d1", "law": "<pcode>", "path": "art_2", "text": "<原文>",
                      "term": "<code>", "definition": "…", "members": ["<code>"]}],
     "statements": [
       {"id": "s1", "law": "<pcode>", "path": "art_7/para_1", "text": "<原文>",
        "kind": "prescriptive", "deontic": "obligation",
        "slots": {"action": {"concept": "discharging"},
                  "subject": [{"literal": "事業"}, {"literal": "負責人"}]},  # 多個值彼此擇一
        "conditions": [{"role": "duty", "clause": 1, "concept": "cod", "comparator": "le",
                        "threshold": 100, "unit": "mg/L", "negated": false, "judgement": false,
                        "delegate": {"law": "<pcode>", "path": "art_2"}}],
        "exception_of": "s0",
        "sanction": {"punishes": "s1", "effect": "fine", "min": 60000, "max": 20000000,
                     "repeating": false, "discretionary": false}}],
     "overrides": [{"winner": "s2", "loser": "s1", "basis": "lex_specialis"}]}

`conditions` 的門檻值恰給一個：`threshold`、`delegate`（委由授權命令的單元）或 `value_concept`。
罰則 `effect` 除處罰種類外有 `order`（改善命令等非處罰的行政命令）；`repeating` 標按次處罰；
`discretionary` 標條文寫「得」的效果裁量。
`judgement` 標要件中的不確定法律概念（情節重大、必要時）：系統不判定，呼叫端給的值視為其判斷，
未給則列為需判斷；限值不得標記。效果面裁量不是要件，用罰則的 `discretionary`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.types.range import Range

from envcite_ontology.settings import DatabaseSettings
from envcite_ontology.settings import load as load_settings

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

EXIT_INVALID = 2


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


def load(conn: Conn, doc: Mapping[str, Any]) -> Loaded:
    """寫入一份標註文件；整份成功或整份不寫入。"""
    try:
        loaded = _load(conn, doc)
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

    ids: dict[str, int] = {}
    conditions = duplicates = 0
    held: list[tuple[Mapping[str, Any], int]] = []
    for s in doc.get("statements", []):
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

    exceptions = overrides = sanctions = 0
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
    for s in doc.get("statements", []):
        if s.get("exception_of") is not None:
            exceptions += conn.execute(
                "INSERT INTO annotation.statement_exception (exception_id, excluded_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (ids[str(s["id"])], ref(str(s["exception_of"]))),
            ).rowcount
    for o in doc.get("overrides", []):
        overrides += conn.execute(
            "INSERT INTO annotation.statement_override"
            " (prevailing_id, subordinate_id, priority_basis)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (ref(str(o["winner"])), ref(str(o["loser"])), o["basis"]),
        ).rowcount
    defined, repeated = _definitions(conn, doc.get("definitions", []), concepts, d_id, review)
    return Loaded(
        len(ids), conditions, exceptions, overrides, sanctions, ids, defined, duplicates + repeated
    )


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
    return {
        str(code): int(cid)  # type: ignore[call-overload]
        for code, cid in conn.execute(
            "SELECT code, id FROM vocab.concept WHERE scheme_id = %s", (scheme_id,)
        )
    }


def _concept(concepts: Mapping[str, int], code: object) -> int:
    if str(code) not in concepts:
        raise AnnotationError(f"引用了不存在的概念：{code}")
    return concepts[str(code)]


def _provision(conn: Conn, law: object, path: object) -> int:
    row = conn.execute(
        "SELECT p.id FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s",
        (law, path),
    ).fetchone()
    if row is None:
        raise AnnotationError(f"語料裡沒有 {law} 的單元 {path}")
    return cast(int, row[0])


def _anchor(conn: Conn, what: str, s: Mapping[str, Any]) -> tuple[int, bytes]:
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
        provision_id, digest = _anchor(conn, "用語定義", t)
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
    provision_id, digest = _anchor(conn, "陳述", s)
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
        "INSERT INTO annotation.claim (claim_kind, derivation_id, confirm_state)"
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
        " (statement_id, slot_role, alternative, concept_id, literal)"
        " VALUES (%s, %s, %s, %s, %s)",
        (
            statement_id,
            slot,
            alternative,
            _concept(concepts, v["concept"]) if v.get("concept") is not None else None,
            v.get("literal"),
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
            "  value_concept_id, negated, delegate_provision_id, needs_assessment)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
            ),
        )
    return len(conditions)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="envcite-annotate")
    parser.add_argument("file", type=Path, help="標註文件（JSON）")
    args = parser.parse_args()

    settings = load_settings(DatabaseSettings)
    try:
        doc = json.loads(args.file.read_text(encoding="utf-8"))
        with psycopg.connect(settings.ontology_dsn) as conn:
            load(conn, doc)
    except (AnnotationError, json.JSONDecodeError) as e:
        logging.getLogger(__name__).error("annotation rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
