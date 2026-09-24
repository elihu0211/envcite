"""管線所依賴的 port。

兩者都是 Protocol，而非 client。管線的形狀——哪個階段可以決定什麼——正是本服務
存在所要固定下來的東西，而這個形狀必須在語言模型端點或執行中的本體服務存在之前
就能測試。實作位於 `adapters.py`；測試中的 fake 是另一種實作，並不次一等。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Article:
    """一筆檢索到的 subdivision。`score` 只在同一次檢索之內可比較。"""

    iri: str
    label: str
    text: str
    score: float


class OntologyPort(Protocol):
    """法規的讀取途徑。本服務中法律事實的唯一來源。"""

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]: ...


class LlmPort(Protocol):
    """生成，受限於封閉的引用代碼集合。

    `allowed_codes` 不是建議。生成的文字會在下游與它比對，集合外的一律刪除，
    因此無視此限制的模型只會讓答案變差，而不會讓答案出錯。
    """

    def generate(
        self, *, question: str, evidence: str, allowed_codes: list[str]
    ) -> Iterator[str]: ...
