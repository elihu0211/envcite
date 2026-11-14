"""一次同步的計數與結果。"""

import json
from collections import Counter
from dataclasses import asdict, dataclass, field


@dataclass
class Report:
    """一次 `envcite-guidance sync` 的計數與結果；`line()` 輸出成日誌的一行 JSON。"""

    source: str
    #: 這次要處理的工作項數（公報的一包、函釋的一列）；已處理過而略過的不計。
    listed: int = 0
    processed: int = 0
    dead_lettered: int = 0
    #: 新寫入的公文件數，不含沿用的。
    documents: int = 0
    #: 已載入過、內容相同而沿用的件數
    repeated: int = 0
    interpretations: int = 0
    cessations: int = 0
    #: 函釋：這次是否為完整的全量走訪；不是全量走訪為 None
    crawl_complete: bool | None = None
    #: 函釋：這次推論下架的件數
    dropped: int = 0
    problems: Counter[str] = field(default_factory=Counter)
    seconds: float = 0.0
    exit_code: int = 0

    def line(self) -> str:
        """報告的一行 JSON（中文不跳脫），寫進日誌。"""
        return json.dumps({**asdict(self), "problems": dict(self.problems)}, ensure_ascii=False)
