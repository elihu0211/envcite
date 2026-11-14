"""待連結罰則：處罰的陳述未確認或沒有指明的罰則，存起來等 `verification.link` 連結。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from envcite_ontology.annotation_document import (
    Document,
    Statement,
)
from envcite_ontology.claims.anchoring import anchor
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.links import ref
from envcite_ontology.db import Conn, fetch_one


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


def find_waiting(conn: Conn, statements: Sequence[Statement], review: str) -> set[str]:
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


def hold(
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
        punished = ref(ids, target) if isinstance(target, str) else target
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
