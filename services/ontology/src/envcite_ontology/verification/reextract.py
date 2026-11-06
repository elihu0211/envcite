"""義務沒有定論的單元重抽：把審查者對上一輪的意見一併交給抽取者。

該重抽的單元由資料推得（`annotation.reextraction_due`），輪數上限是資料庫的 CHECK
（`extraction_attempt.round`），不在這裡。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import psycopg
from psycopg.types.range import Range

Conn = psycopg.Connection[tuple[object, ...]]

#: 一個陳述最多帶幾條審查意見、每條最多多長：太長會蓋過原文。
MAX_NOTES = 6
MAX_NOTE_CHARS = 240


@dataclass(frozen=True)
class Due:
    law: str
    path: str
    round: int


def due_units(conn: Conn, agent: str, version: str, law: str) -> list[Due]:
    """法規裡該重抽的單元，依閱讀順序。"""
    rows = conn.execute(
        "SELECT p.path, d.next_round FROM annotation.reextraction_due AS d"
        "  JOIN corpus.provision AS p ON p.id = d.provision_id"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id"
        "   AND v.content_sha256 = d.content_sha256 AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s"
        "   AND d.producer = %s AND d.producer_version = %s"
        " ORDER BY p.sort_key, p.path",
        (law, agent, version),
    )
    return [Due(law, str(path), int(cast(int, nxt))) for path, nxt in rows]


def _reasons(method: str, evidence: Any) -> list[str]:
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


def review_notes(conn: Conn, provision_id: int, sha256: bytes) -> list[str]:
    """單元現行文字上沒有定論的義務各一段：它的引文，與審查者指出的問題。"""
    out: list[str] = []
    rows = conn.execute(
        "SELECT u.statement_id, v.content, s.span FROM annotation.unresolved_duty AS u"
        "  JOIN annotation.statement AS s ON s.id = u.statement_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = u.provision_id"
        "   AND v.content_sha256 = u.content_sha256 AND v.superseded_at IS NULL"
        " WHERE u.provision_id = %s AND u.content_sha256 = %s ORDER BY u.statement_id",
        (provision_id, sha256),
    ).fetchall()
    for sid, content, raw_span in rows:
        span = cast(Range[int] | None, raw_span)
        text = str(content)
        quote = text if span is None else text[span.lower : span.upper]
        reasons: list[str] = []
        for method, evidence in conn.execute(
            "SELECT DISTINCT ON (method) method, evidence FROM annotation.verification"
            " WHERE assertion_id = %s AND result = 'fail' AND method <> 'policy'"
            " ORDER BY method, id DESC",
            (sid,),
        ):
            reasons += _reasons(str(method), evidence)
        shown = "；".join(r[:MAX_NOTE_CHARS] for r in reasons[:MAX_NOTES]) or "（沒有給出理由）"
        out.append(f"引文「{quote}」未被接受：{shown}")
    return out
