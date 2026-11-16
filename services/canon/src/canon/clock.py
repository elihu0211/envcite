"""現在：邏輯收注入的時鐘，不直接讀系統時鐘，測試才能固定它。

只有這個模組呼叫 `date.today` 與 `datetime.now`。
"""

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

type Today = Callable[[], date]
type Now = Callable[[], datetime]

_TAIPEI = ZoneInfo("Asia/Taipei")


def taipei_now() -> datetime:
    """臺灣時間的現在。裁判書 API 的服務時段以此時區計。"""
    return datetime.now(_TAIPEI)
