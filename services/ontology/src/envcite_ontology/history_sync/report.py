"""一次同步的計數，與整批驗收越線的例外。"""

import json
from collections import Counter
from dataclasses import dataclass, field


class BatchRejected(Exception):
    """整批比例越線：轉錄的格式或回推規則跟不上來源。"""


@dataclass
class Report:
    """一次 `envcite-history` 的計數；`line()` 輸出成日誌的一行 JSON。"""

    listed: int = 0
    fetched_pages: int = 0
    reused_pages: int = 0
    snapshots: int = 0
    dead_lettered: int = 0
    matched: int = 0
    skipped: int = 0
    #: 寫入動作 → 部數：`writer.write` 的回傳，另有沒寫的 `rejected` 與被資料庫擋下的
    #: `write_rejected`。
    actions: Counter[str] = field(default_factory=Counter)
    versions: int = 0
    units: int = 0
    #: 現行頁與全國法規資料庫現行文字的核對結果 → 部數。
    check: Counter[str] = field(default_factory=Counter)
    problems: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0

    def line(self) -> str:
        """報告的一行 JSON（中文不跳脫），寫進日誌。"""
        return json.dumps(
            {k: dict(v) if isinstance(v, Counter) else v for k, v in self.__dict__.items()},
            ensure_ascii=False,
        )
