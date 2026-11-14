"""標註列的共用寫入原語：主張列、封存、時段與單值查詢。"""

from psycopg.types.range import Range

from envcite_ontology.annotation_document import (
    Span,
)
from envcite_ontology.db import Conn, scalar


def one(conn: Conn, sql: str, *args: object) -> int:
    """執行回傳單一整數的敘述（新列的 id 等）。"""
    return scalar(conn, sql, args, int)


def seal(conn: Conn, claim: int) -> int:
    """封存主張的內容；回傳留下的主張 id（已有內容相同的未駁回主張時是那一個）。"""
    return one(conn, "SELECT annotation.seal(%s)", claim)


def new_claim(conn: Conn, kind: str, derivation_id: int, review: str) -> int:
    """主張本身（來源推導與審核狀態）；子型別的列以同一個 id 寫入。"""
    return one(
        conn,
        "INSERT INTO annotation.assertion (assertion_kind, derivation_id, confirm_state)"
        " VALUES (%s, %s, %s) RETURNING id",
        kind,
        derivation_id,
        review,
    )


def span_range(span: Span) -> Range[int] | None:
    """原文區段 `[起, 迄)` 對應資料庫的區間；沒有區段則為 None。"""
    return None if span is None else Range(span[0], span[1])
