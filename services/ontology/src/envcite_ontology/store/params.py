"""查詢的共用小工具：`as_of` 參數與呼叫期限。"""

import time


def as_of_param(as_of: str) -> str:
    """空 `as_of` 為今日，由資料庫解析以與查詢同一時鐘。"""
    return as_of or "today"


def expiry(timeout: float | None) -> float | None:
    """`timeout` 秒後的 monotonic 時刻；沒有期限為 None。"""
    if timeout is None:
        return None
    return time.monotonic() + timeout


def time_left(deadline: float | None) -> float | None:
    """距離 `deadline` 還剩的秒數；沒有期限為 None。"""
    if deadline is None:
        return None
    return deadline - time.monotonic()
