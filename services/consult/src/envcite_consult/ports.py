"""管線所依賴的 port。

它是 Protocol，而非 client。管線的形狀——哪個階段可以決定什麼——正是本服務存在
所要固定下來的東西，而這個形狀必須在執行中的本體服務存在之前就能測試。實作位於
`adapters.py`；測試中的 fake 是另一種實作，並不次一等。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Article:
    """一筆檢索到的 subdivision。`score` 只在同一次檢索之內可比較。"""

    iri: str
    label: str
    text: str
    score: float
    lexical_score: float = 0.0
    semantic_similarity: float | None = None


class OntologyPort(Protocol):
    """法規的讀取途徑。本服務中法律事實的唯一來源。"""

    def search(
        self, query: str, *, as_of: str = "", limit: int = 10, timeout: float | None = None
    ) -> list[Article]:
        """`timeout` 是呼叫端剩下的秒數上限；None 為只受實作自己的上限約束。"""
        ...
