"""單項隔離：記下失敗的項目（識別、錯誤、次數）後繼續。"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DeadLetter:
    item_id: str
    error: str
    # "item"：單項問題；"retries_exhausted"：端點錯誤重試用盡。
    kind: str
    attempts: int
    at: float = field(default_factory=time.time)


class DeadLetterSink(Protocol):
    """隔離表的寫入端。資料庫實作的表名取自 `Policy.dead_letter_table`。"""

    def record(self, entry: DeadLetter) -> None: ...


class MemoryDeadLetterSink:
    def __init__(self) -> None:
        self.entries: list[DeadLetter] = []

    def record(self, entry: DeadLetter) -> None:
        self.entries.append(entry)


class JsonlDeadLetterSink:
    """每筆一行 JSON，附加寫入。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = path

    def record(self, entry: DeadLetter) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
