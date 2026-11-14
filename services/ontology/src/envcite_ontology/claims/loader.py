"""標註文件的載入入口：驗證、寫入與交易邊界。"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

import psycopg
from pydantic import ValidationError

from envcite_ontology.annotation_document import (
    Document,
)
from envcite_ontology.claims.definitions import write_definitions
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.held import find_waiting, hold
from envcite_ontology.claims.links import link_exceptions, link_overrides
from envcite_ontology.claims.rows import one
from envcite_ontology.claims.sanctions import link_triggers, write_sanctions
from envcite_ontology.claims.statements import write_statements
from envcite_ontology.claims.vocabulary import write_concepts
from envcite_ontology.db import Conn

log = logging.getLogger(__name__)


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


def _write(conn: Conn, raw: Mapping[str, object], doc: Document) -> Loaded:
    derivation = doc.derivation
    d_id = one(
        conn,
        "INSERT INTO annotation.derivation (method, producer, producer_version)"
        " VALUES (%s, %s, %s) RETURNING id",
        derivation.method,
        derivation.agent,
        derivation.agent_version,
    )
    concepts = write_concepts(conn, doc.scheme, doc.concepts)

    waiting = find_waiting(conn, doc.statements, doc.review)
    statements = [s for s in doc.statements if s.id not in waiting]
    written = write_statements(conn, statements, concepts, d_id, doc.review)
    ids = written.ids
    sanctions = write_sanctions(conn, written.sanctioned, ids)
    # 例外、優先與罰則前提是陳述之間的關係，兩方都封存、id 確定之後才寫；沿用既有主張時關係可能已在
    exceptions = link_exceptions(conn, statements, ids)
    triggers = link_triggers(conn, written.sanctioned, ids)
    overrides = link_overrides(conn, doc.overrides, ids, waiting)
    kept_back = hold(conn, raw, doc, [s for s in doc.statements if s.id in waiting], ids)
    defined, repeated = write_definitions(conn, doc.definitions, concepts, d_id, doc.review)
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
