"""指標介面。只定義 gauge 與日誌實作，不引入外部依賴；接 OTel 的實作由服務自己提供。

指標名稱：
- jobkit_breaker_state：0 closed、1 half_open、2 open
- jobkit_dead_letter_total：本次執行隔離的項目數
- jobkit_budget_remaining_seconds／jobkit_budget_remaining_items
"""

import logging
from typing import Protocol

logger = logging.getLogger("envcite_jobkit")


class Metrics(Protocol):
    def gauge(self, name: str, value: float) -> None: ...


class LogMetrics:
    def gauge(self, name: str, value: float) -> None:
        logger.info("metric %s=%s", name, value)


class NullMetrics:
    def gauge(self, name: str, value: float) -> None:
        pass
