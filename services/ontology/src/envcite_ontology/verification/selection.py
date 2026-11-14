"""要處理什麼：流程鎖、待處理的法規、法規的單元，與已抽過的判斷。"""

from typing import NamedTuple

from envcite_ontology.db import Conn, fetch, scalar


class _PathRow(NamedTuple):
    path: str


class _LawRow(NamedTuple):
    local_id: str


_UNITS = (
    "SELECT p.path FROM corpus.provision AS p"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND EXISTS ("
    "   SELECT 1 FROM corpus.provision_version AS v"
    "    WHERE v.provision_id = p.id AND v.superseded_at IS NULL AND v.content IS NOT NULL)"
    " ORDER BY p.sort_key, p.path"
)


def units_of(conn: Conn, law: str) -> list[str]:
    """法規現行、有文字的單元路徑，依閱讀順序。"""
    return [r.path for r in fetch(conn, _UNITS, (law,), _PathRow)]


#: 同一時間只讓一個流程花模型費用；鍵是任意固定常數，跨連線共用。
_LOCK_KEY = 0x656E764C  # "envL"


def try_lock(conn: Conn) -> bool:
    """取得流程的諮詢鎖；已有另一個流程在跑時回 False。鎖隨連線結束釋放。"""
    return scalar(conn, "SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,), bool)


_PENDING_LAWS = (
    "SELECT r.local_id FROM corpus.legal_resource AS r"
    " WHERE r.id_scheme = 'moj_pcode' AND r.repealed_on IS NULL AND EXISTS ("
    "   SELECT 1 FROM corpus.provision AS p"
    "     JOIN corpus.provision_version AS v ON v.provision_id = p.id"
    "    WHERE p.resource_id = r.id AND v.superseded_at IS NULL AND v.content IS NOT NULL"
    "      AND NOT EXISTS ("
    "        SELECT 1 FROM annotation.statement AS s"
    "          JOIN annotation.assertion AS sc ON sc.id = s.id"
    "          JOIN annotation.derivation AS d ON d.id = sc.derivation_id"
    "         WHERE s.provision_id = p.id AND s.content_sha256 = v.content_sha256"
    "           AND d.producer = %(agent)s AND d.producer_version = %(version)s)"
    "      AND NOT EXISTS ("
    "        SELECT 1 FROM annotation.extraction_attempt AS a"
    "         WHERE a.provision_id = p.id AND a.content_sha256 = v.content_sha256"
    "           AND a.producer = %(agent)s AND a.producer_version = %(version)s))"
    " ORDER BY r.local_id LIMIT %(limit)s"
)


def pending_laws(conn: Conn, agent: str, version: str, limit: int | None = None) -> list[str]:
    """還有單元未被此抽取者與版本抽過的現行法規 pcode，依 pcode 排序。

    待辦由資料推得，不另存佇列：單元的現行文字被抽過（有同抽取者與版本的陳述錨在它的雜湊上）
    就不算待辦，所以文字更新後單元自動回到待辦。`limit` 限制一次處理幾部法規，用來控制費用。
    """
    params = {"agent": agent, "version": version, "limit": limit}
    return [r.local_id for r in fetch(conn, _PENDING_LAWS, params, _LawRow)]


def already_extracted(conn: Conn, law: str, path: str, agent: str, version: str) -> bool:
    """此抽取者與版本已對單元的現行文字答過：有陳述錨在它，或留有抽取紀錄（可能一個陳述也沒有）。"""
    row = conn.execute(
        "SELECT 1 FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE r.local_id = %(law)s AND p.path = %(path)s AND ("
        "   EXISTS (SELECT 1 FROM annotation.statement AS s"
        "             JOIN annotation.assertion AS sc ON sc.id = s.id"
        "             JOIN annotation.derivation AS d ON d.id = sc.derivation_id"
        "            WHERE s.provision_id = p.id AND s.content_sha256 = v.content_sha256"
        "              AND d.producer = %(agent)s AND d.producer_version = %(version)s)"
        "   OR EXISTS (SELECT 1 FROM annotation.extraction_attempt AS a"
        "               WHERE a.provision_id = p.id AND a.content_sha256 = v.content_sha256"
        "                 AND a.producer = %(agent)s AND a.producer_version = %(version)s))"
        " LIMIT 1",
        {"law": law, "path": path, "agent": agent, "version": version},
    ).fetchone()
    return row is not None
