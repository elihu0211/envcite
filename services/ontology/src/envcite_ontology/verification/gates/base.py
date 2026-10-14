"""閘門共用型別與陳述所在條文單元的讀取。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import psycopg

from envcite_ontology.verification.context import ancestors_text

Conn = psycopg.Connection[tuple[object, ...]]
Verdict = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    evidence: dict[str, Any] = field(default_factory=dict)


class Gate(Protocol):
    """`check` 回 None 為不適用；`verifier` 記入紀錄，不得等於抽取者。"""

    @property
    def method(self) -> str: ...

    @property
    def verifier(self) -> str: ...

    def check(self, conn: Conn, statement_id: int) -> GateResult | None: ...


@dataclass(frozen=True)
class Unit:
    statement_id: int
    provision_id: int
    path: str
    resource_id: int
    text: str
    span: tuple[int, int] | None
    kind: str
    deontic: str | None
    context: str = ""  # 上層條文，只供讀；錨點與引文在 text

    @property
    def quoted(self) -> str:
        return self.text if self.span is None else self.text[self.span[0] : self.span[1]]

    @property
    def with_context(self) -> str:
        """含上層的引文：罰則額度可能寫在上層的項，款自己的引文沒有。"""
        return f"{self.context}\n{self.quoted}" if self.context else self.quoted


def unit_of(conn: Conn, statement_id: int) -> Unit:
    row = conn.execute(
        "SELECT s.provision_id, p.path, p.resource_id, v.content, lower(s.span), upper(s.span),"
        "       s.statement_kind::text, s.norm_modality::text"
        "  FROM annotation.statement AS s"
        "  JOIN corpus.provision AS p ON p.id = s.provision_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE s.id = %s LIMIT 1",
        (statement_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"statement {statement_id} has no current text to check against")
    pid, path, rid, text, lo, hi, kind, deontic = row
    span = None if lo is None else (cast(int, lo), cast(int, hi))
    return Unit(
        statement_id,
        cast(int, pid),
        cast(str, path),
        cast(int, rid),
        cast(str, text),
        span,
        cast(str, kind),
        cast("str | None", deontic),
        ancestors_text(conn, cast(int, pid)),
    )
