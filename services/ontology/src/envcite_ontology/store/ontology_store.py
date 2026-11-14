"""資料層的組合：一個連線池，與接在它上面的各個儲存庫。

這裡只做接線，沒有查詢邏輯；消費端拿它的 `laws`、`graph`、`retrieval`（以 `ports` 的 Protocol
標註），需要連線期限的拿 `connections`。可用 `with` 在結束時關閉連線池。
"""

from dataclasses import dataclass
from types import TracebackType
from typing import Self

from envcite_ontology.store.connections import Connections
from envcite_ontology.store.graph import GraphRepository
from envcite_ontology.store.laws import LawRepository
from envcite_ontology.store.retrieval import RetrievalRepository


@dataclass(frozen=True, slots=True)
class OntologyStore:
    """連線池與它上面的儲存庫。"""

    connections: Connections
    laws: LawRepository
    graph: GraphRepository
    retrieval: RetrievalRepository

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> Self:
        """以 DSN 建立並開啟連線池，接上各個儲存庫。"""
        return cls.over(Connections.from_dsn(dsn, min_size=min_size, max_size=max_size))

    @classmethod
    def over(cls, connections: Connections) -> Self:
        """把儲存庫接到既有的連線池上。"""
        laws = LawRepository(connections)
        return cls(
            connections, laws, GraphRepository(connections, laws), RetrievalRepository(connections)
        )

    def close(self) -> None:
        """關閉連線池。"""
        self.connections.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
