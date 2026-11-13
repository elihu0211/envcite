"""閘門共用型別與陳述所在條文單元的讀取。"""

from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple, Protocol

from envcite_ontology.db import Conn, fetch_one
from envcite_ontology.verification.context import ancestors_text

type Verdict = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class GateResult:
    """閘門對一個被檢查物的結論；`evidence` 以 JSON 原樣寫進驗證紀錄。"""

    verdict: Verdict
    evidence: dict[str, Any] = field(default_factory=dict)


class Gate(Protocol):
    """`check` 回 None 為不適用；`verifier` 記入紀錄，不得等於抽取者。"""

    @property
    def method(self) -> str:
        """驗證方法名；政策的必要方法與驗證紀錄以它指名這個閘門。"""
        ...

    @property
    def verifier(self) -> str:
        """記入驗證紀錄的驗證者識別（閘門名與版本，模型閘門再加模型名）。"""
        ...

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """檢查一個陳述；不適用回 None。丟 `ModelError` 時呼叫端記為 inconclusive。"""
        ...


@dataclass(frozen=True)
class Unit:
    """陳述與它所錨定的條文單元現行原文。"""

    statement_id: int
    provision_id: int
    path: str
    resource_id: int
    text: str
    #: 引文在 `text` 內的（起，迄）區間；None 表示陳述涵蓋整段原文
    span: tuple[int, int] | None
    kind: str
    deontic: str | None
    context: str = ""  # 上層條文，只供讀；錨點與引文在 text

    @property
    def quoted(self) -> str:
        """陳述的引文；沒有 `span` 時是整段原文。"""
        return self.text if self.span is None else self.text[self.span[0] : self.span[1]]

    @property
    def with_context(self) -> str:
        """含上層的引文：罰則額度可能寫在上層的項，款自己的引文沒有。"""
        return f"{self.context}\n{self.quoted}" if self.context else self.quoted


_UNIT = (
    "SELECT s.provision_id, p.path, p.resource_id, v.content, lower(s.span), upper(s.span),"
    "       s.statement_kind::text, s.norm_modality::text"
    "  FROM annotation.statement AS s"
    "  JOIN corpus.provision AS p ON p.id = s.provision_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
    "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
    " WHERE s.id = %s LIMIT 1"
)


class _UnitRow(NamedTuple):
    provision_id: int
    path: str
    resource_id: int
    text: str
    #: 引文的區間；陳述涵蓋整段原文時兩端皆 NULL
    span_start: int | None
    span_end: int | None
    kind: str
    deontic: str | None


def unit_of(conn: Conn, statement_id: int) -> Unit:
    """讀出陳述所錨定單元的現行原文與上層條文。

    陳述錨定的版本已被取代（或陳述不存在）時沒有可核對的原文，丟 `LookupError`。
    """
    row = fetch_one(conn, _UNIT, (statement_id,), _UnitRow)
    if row is None:
        raise LookupError(f"statement {statement_id} has no current text to check against")
    span = (
        None if row.span_start is None or row.span_end is None else (row.span_start, row.span_end)
    )
    return Unit(
        statement_id,
        row.provision_id,
        row.path,
        row.resource_id,
        row.text,
        span,
        row.kind,
        row.deontic,
        ancestors_text(conn, row.provision_id),
    )
