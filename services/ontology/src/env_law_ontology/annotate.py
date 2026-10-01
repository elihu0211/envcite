"""標註文件的載入（env-law-docs 02-04 §6、02-05 §5）。

規範陳述、要件、但書、優先與罰則寫成一份 JSON 文件（由抽取器產生，見 `verification/extract.py`），
這裡把它寫進 `annotation` 與 `vocab`。載入只負責寫入與錨定，不負責接受：陳述是否成為推論用的
事實，由 `verification` 的閘門與政策決定。

**錨點是文字，不是版本列。** 每個陳述要附上它所讀的那段條文原文 `text`：載入時算出雜湊，
並要求該單元現行的某個版本列確有這個雜湊，否則拒絕——標註的是不存在於語料的文字，
等於憑空標註。之後效力列更替而文字沒變，陳述沿用；文字改了，雜湊對不上，陳述自動不在
該日的事實裡（`annotation.statements_at`），須重新標註。

**載入不接受任何陳述。** 陳述的狀態取文件的 `review`（預設 `extracted`）；下游只取已確認的
陳述（或規則推導者），所以沒寫 `confirmed` 的文件載入後不影響任何推論。

一次載入記為一個推導活動，在同一個交易內完成：任何一處不合，整份不寫入。載入不是冪等的：
同一份文件載入兩次得到兩組陳述；要修正，把舊陳述的 `review` 改為 `rejected` 再載入新的。

文件格式：

    {"derivation": {"method": "llm", "agent": "…", "agent_version": "…"},
     "review": "confirmed",
     "scheme": {"code": "env", "title": "…"},
     "concepts": [{"code": "cod", "label": "…", "broader": "<code>", "alt": ["…"]}],
     "statements": [
       {"id": "s1", "law": "<pcode>", "path": "art_7/para_1", "text": "<原文>",
        "kind": "prescriptive", "deontic": "obligation",
        "slots": {"action": {"concept": "discharging"}, "object": {"literal": "…"}},
        "conditions": [{"role": "duty", "clause": 1, "concept": "cod", "comparator": "le",
                        "threshold": 100, "unit": "mg/L", "negated": false, "judgement": false,
                        "delegate": {"law": "<pcode>", "path": "art_2"}}],
        "exception_of": "s0",
        "sanction": {"punishes": "s1", "effect": "fine", "min": 60000, "max": 20000000,
                     "repeating": false}}],
     "overrides": [{"winner": "s2", "loser": "s1", "basis": "lex_specialis"}]}

`conditions` 的門檻值恰給一個：`threshold`、`delegate`（委由授權命令的單元）或 `value_concept`。
罰則的 `effect` 除處罰種類外有 `order`（改善命令等非處罰的行政命令）；
`repeating: true` 標出按次處罰。
`judgement: true` 標出不確定法律概念或裁量（情節重大、必要時）：系統不從事實判定它，呼叫端提供
的值視為其判斷，沒有提供則列為需判斷；限值不得標記。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.types.range import Range

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
    except (KeyError, TypeError) as e:
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
        "INSERT INTO annotation.derivation (method, agent, agent_version)"
        " VALUES (%s, %s, %s) RETURNING id",
        derivation["method"],
        derivation["agent"],
        derivation["agent_version"],
    )
    concepts = _concepts(conn, doc.get("scheme"), doc.get("concepts", []))

    ids: dict[str, int] = {}
    conditions = 0
    for s in doc.get("statements", []):
        sid = str(s["id"])
        if sid in ids:
            raise AnnotationError(f"陳述 id 重複：{sid}")
        ids[sid] = _statement(conn, s, d_id, review)
        _slots(conn, ids[sid], s.get("slots", {}), concepts)
        conditions += _conditions(conn, ids[sid], s.get("conditions", []), concepts)

    def ref(key: str) -> int:
        if key not in ids:
            raise AnnotationError(f"引用了不存在的陳述：{key}")
        return ids[key]

    exceptions = overrides = sanctions = 0
    for s in doc.get("statements", []):
        if s.get("exception_of") is not None:
            conn.execute(
                "INSERT INTO annotation.statement_exception (exception_id, excluded_id)"
                " VALUES (%s, %s)",
                (ids[str(s["id"])], ref(str(s["exception_of"]))),
            )
            exceptions += 1
        if (sanction := s.get("sanction")) is not None:
            conn.execute(
                "INSERT INTO annotation.sanction"
                " (statement_id, punishes_id, effect, amount_text, min_amount, max_amount,"
                "  repeating)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    ids[str(s["id"])],
                    int(sanction["punishes_id"])
                    if sanction.get("punishes_id") is not None
                    else ref(str(sanction["punishes"])),
                    sanction["effect"],
                    sanction.get("amount_text"),
                    sanction.get("min"),
                    sanction.get("max"),
                    bool(sanction.get("repeating", False)),
                ),
            )
            sanctions += 1
    for o in doc.get("overrides", []):
        conn.execute(
            "INSERT INTO annotation.statement_override (winner_id, loser_id, basis)"
            " VALUES (%s, %s, %s)",
            (ref(str(o["winner"])), ref(str(o["loser"])), o["basis"]),
        )
        overrides += 1
    return Loaded(len(ids), conditions, exceptions, overrides, sanctions, ids)


def _one(conn: Conn, sql: str, *args: object) -> int:
    row = conn.execute(sql, args).fetchone()
    assert row is not None
    return cast(int, row[0])


def _concepts(
    conn: Conn, scheme: Mapping[str, Any] | None, concepts: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    """文件的概念與資料庫裡該詞彙方案已有的概念；以代碼為鍵。"""
    if scheme is None:
        if concepts:
            raise AnnotationError("有概念但沒有 scheme")
        return {}
    scheme_id = _one(
        conn,
        "INSERT INTO vocab.concept_scheme (code, title) VALUES (%s, %s)"
        " ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        scheme["code"],
        scheme["title"],
    )
    for c in concepts:
        cid = _one(
            conn,
            "INSERT INTO vocab.concept (scheme_id, code, pref_label) VALUES (%s, %s, %s)"
            " ON CONFLICT (scheme_id, code) DO UPDATE SET pref_label = EXCLUDED.pref_label"
            " RETURNING id",
            scheme_id,
            c["code"],
            c["label"],
        )
        for alt in c.get("alt", []):
            conn.execute(
                "INSERT INTO vocab.concept_label (concept_id, label, kind)"
                " VALUES (%s, %s, 'alt') ON CONFLICT DO NOTHING",
                (cid, alt),
            )
    for c in concepts:  # 上位概念可以引用同一份文件稍後才列出的概念
        if c.get("broader") is not None:
            conn.execute(
                "UPDATE vocab.concept SET broader_id ="
                " (SELECT id FROM vocab.concept WHERE scheme_id = %s AND code = %s)"
                " WHERE scheme_id = %s AND code = %s",
                (scheme_id, c["broader"], scheme_id, c["code"]),
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


def _statement(conn: Conn, s: Mapping[str, Any], derivation_id: int, review: str) -> int:
    provision_id = _provision(conn, s["law"], s["path"])
    digest = hashlib.sha256(str(s["text"]).encode()).digest()
    current = conn.execute(
        "SELECT 1 FROM corpus.provision_version"
        " WHERE provision_id = %s AND content_sha256 = %s AND superseded_at IS NULL",
        (provision_id, digest),
    ).fetchone()
    if current is None:
        raise AnnotationError(
            f"陳述 {s['id']} 錨定的文字不是 {s['law']} {s['path']} 現行任何版本的文字"
        )
    span = s.get("span")
    return _one(
        conn,
        "INSERT INTO annotation.statement"
        " (provision_id, content_sha256, span, kind, deontic, derivation_id, review)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        provision_id,
        digest,
        Range(span[0], span[1]) if span else None,
        s["kind"],
        s.get("deontic"),
        derivation_id,
        review,
    )


def _slots(
    conn: Conn,
    statement_id: int,
    slots: Mapping[str, Mapping[str, Any]],
    concepts: Mapping[str, int],
) -> None:
    for slot, v in slots.items():
        conn.execute(
            "INSERT INTO annotation.statement_slot (statement_id, slot, concept_id, literal)"
            " VALUES (%s, %s, %s, %s)",
            (
                statement_id,
                slot,
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
            " (statement_id, role, clause, concept_id, comparator, threshold, unit,"
            "  value_concept_id, negated, delegate_provision_id, judgement)"
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
    parser = argparse.ArgumentParser(prog="env-law-annotate")
    parser.add_argument("file", type=Path, help="標註文件（JSON）")
    args = parser.parse_args()

    dsn = os.environ.get("ONTOLOGY_DSN", "")
    if not dsn:
        raise SystemExit("ONTOLOGY_DSN is required")
    try:
        doc = json.loads(args.file.read_text(encoding="utf-8"))
        with psycopg.connect(dsn) as conn:
            load(conn, doc)
    except (AnnotationError, json.JSONDecodeError) as e:
        logging.getLogger(__name__).error("annotation rejected: %s", e)
        raise SystemExit(EXIT_INVALID) from e
