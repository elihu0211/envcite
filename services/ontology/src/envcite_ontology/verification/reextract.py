"""義務沒有定論的單元重抽：把審查者對上一輪的意見一併交給抽取者。

該重抽的單元由資料推得（`annotation.reextraction_due`），輪數上限是資料庫的 CHECK
（`extraction_attempt.round`），不在這裡。
"""

from dataclasses import dataclass
from typing import NamedTuple

from psycopg.types.range import Range

from envcite_ontology.db import Conn, fetch

#: 一個陳述最多帶幾條審查意見、每條最多多長：太長會蓋過原文。
MAX_NOTES = 6
MAX_NOTE_CHARS = 240


@dataclass(frozen=True)
class Due:
    """一個該重抽的單元：所屬法規、單元路徑，與這次重抽的輪號。"""

    law: str
    path: str
    round: int


class _DueRow(NamedTuple):
    path: str
    next_round: int


_DUE = (
    "SELECT p.path, d.next_round FROM annotation.reextraction_due AS d"
    "  JOIN corpus.provision AS p ON p.id = d.provision_id"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = p.id"
    "   AND v.content_sha256 = d.content_sha256 AND v.superseded_at IS NULL"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s"
    "   AND d.producer = %s AND d.producer_version = %s"
    " ORDER BY p.sort_key, p.path"
)


def due_units(conn: Conn, agent: str, version: str, law: str) -> list[Due]:
    """法規裡該重抽的單元，依閱讀順序。"""
    return [
        Due(law, r.path, r.next_round) for r in fetch(conn, _DUE, (law, agent, version), _DueRow)
    ]


def _reasons(method: str, evidence: object) -> list[str]:
    """一次失敗的驗證裡，審查者說的問題。"""
    if not isinstance(evidence, dict):
        return []
    if method == "panel":
        return [
            str(issue)
            for vote in evidence.get("votes", [])
            if isinstance(vote, dict) and vote.get("faithful") is False
            for issue in (vote.get("issues") or [])
        ]
    if method == "roundtrip":
        return [f"由標註重寫出的句子與原文不同：{evidence.get('differences')}"]
    return []


class _UnresolvedRow(NamedTuple):
    statement_id: int
    content: str
    #: 引文的區間；沒有區間的陳述涵蓋整段原文
    span: Range[int] | None


class _FailureRow(NamedTuple):
    method: str
    evidence: object


_UNRESOLVED = (
    "SELECT u.statement_id, v.content, s.span FROM annotation.unresolved_duty AS u"
    "  JOIN annotation.statement AS s ON s.id = u.statement_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = u.provision_id"
    "   AND v.content_sha256 = u.content_sha256 AND v.superseded_at IS NULL"
    " WHERE u.provision_id = %s AND u.content_sha256 = %s ORDER BY u.statement_id"
)

_FAILURES = (
    "SELECT DISTINCT ON (method) method, evidence FROM annotation.verification"
    " WHERE assertion_id = %s AND result = 'fail' AND method <> 'policy'"
    " ORDER BY method, id DESC"
)


def review_notes(conn: Conn, provision_id: int, sha256: bytes) -> list[str]:
    """單元現行文字上沒有定論的義務各一段：它的引文，與審查者指出的問題。"""
    out: list[str] = []
    for duty in fetch(conn, _UNRESOLVED, (provision_id, sha256), _UnresolvedRow):
        span = duty.span
        quote = duty.content if span is None else duty.content[span.lower : span.upper]
        reasons: list[str] = []
        for failure in fetch(conn, _FAILURES, (duty.statement_id,), _FailureRow):
            reasons += _reasons(failure.method, failure.evidence)
        shown = "；".join(r[:MAX_NOTE_CHARS] for r in reasons[:MAX_NOTES]) or "（沒有給出理由）"
        out.append(f"引文「{quote}」未被接受：{shown}")
    return out
