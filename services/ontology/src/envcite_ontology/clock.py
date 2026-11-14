"""今天的日期：邏輯收注入的 `Today`，不直接讀系統時鐘，測試才能固定它。"""

from collections.abc import Callable
from datetime import date

type Today = Callable[[], date]
