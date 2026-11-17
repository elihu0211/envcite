"""確定性規則抽取器的共同形狀。

抽取器是純規則（不用模型、不猜）：把語料裡的條文轉成 `annotation` 的結構，冪等，改了解析規則
要遞增 `version`。解析與寫入各抽取器自己持有，因為表不同；這裡只放它們共有的三件事：識別
（`name`、`version`）、結果的計數，與依序執行並留下一行紀錄。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from canon.db import Conn

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Extracted:
    """一次抽取的結果：新寫入的列數、其中對不到而留空的，以及已有而略過的。"""

    written: int
    unresolved: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class Extractor:
    """一種確定性規則：識別、版本與冪等的 `extract`。

    `extract` 不 commit，由呼叫端決定；重跑只寫新的，已有的（含人駁回的）不復活。
    """

    #: 推導來源的識別；寫進 `annotation.derivation.producer` 的值，已寫入的列不可改名。
    name: str
    #: 改了解析規則就遞增。資料庫存成文字的欄位（`derivation.producer_version`）以 `str()` 寫入。
    version: int
    extract: Callable[[Conn], Extracted]

    def __post_init__(self) -> None:
        """版本從 1 起算。"""
        if self.version < 1:
            raise ValueError(f"{self.name}: version must be positive, got {self.version}")

    @property
    def derivation(self) -> dict[str, str]:
        """載入器（`guidance.load` 等）收的 `derivation` 文件：規則推導，不是人或模型。"""
        return {"method": "rule", "agent": self.name, "agent_version": str(self.version)}


def run(conn: Conn, *extractors: Extractor) -> dict[str, Extracted]:
    """依序執行抽取器，各記一行紀錄；回傳名稱 → 結果。不 commit。"""
    results: dict[str, Extracted] = {}
    for extractor in extractors:
        started = time.monotonic()
        result = results[extractor.name] = extractor.extract(conn)
        log.info(
            "%s v%d：新寫入 %d（對不到 %d），略過 %d，%.1f 秒",
            extractor.name,
            extractor.version,
            result.written,
            result.unresolved,
            result.skipped,
            time.monotonic() - started,
        )
    return results
