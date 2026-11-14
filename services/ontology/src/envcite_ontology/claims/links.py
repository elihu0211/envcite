"""陳述之間的關係：文件裡的 id 解析，以及例外與優先關係的寫入。"""

from collections.abc import Mapping, Sequence

from envcite_ontology.annotation_document import (
    Override,
    Statement,
)
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.db import Conn


def ref(ids: Mapping[str, int], key: str) -> int:
    """文件裡的陳述 id 對資料庫的陳述編號；引用不存在的陳述則拒絕。"""
    if key not in ids:
        raise AnnotationError(f"引用了不存在的陳述：{key}")
    return ids[key]


def link_exceptions(conn: Conn, statements: Sequence[Statement], ids: Mapping[str, int]) -> int:
    """新增的例外關係數。"""
    exceptions = 0
    for s in statements:
        if s.exception_of is not None:
            exceptions += conn.execute(
                "INSERT INTO annotation.statement_exception (exception_id, excluded_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (ids[s.id], ref(ids, s.exception_of)),
            ).rowcount
    return exceptions


def link_overrides(
    conn: Conn, overrides: Sequence[Override], ids: Mapping[str, int], waiting: set[str]
) -> int:
    """新增的優先關係數；涉及待連結罰則的優先關係拒絕。"""
    written = 0
    for o in overrides:
        written += conn.execute(
            "INSERT INTO annotation.statement_override"
            " (prevailing_id, subordinate_id, priority_basis)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (ref(ids, o.winner), ref(ids, o.loser), o.basis),
        ).rowcount
    for o in overrides:
        if {o.winner, o.loser} & waiting:
            raise AnnotationError(f"優先關係涉及待連結的罰則：{o.winner}、{o.loser}")
    return written
