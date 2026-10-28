"""呼叫期限的讀取。"""

from __future__ import annotations

import grpc

#: 沒有期限時，`time_remaining()` 回的是一個極大的數而不是 None；超過一天都當作沒有。
NO_DEADLINE_SECONDS = 86400.0


def remaining_seconds(context: grpc.ServicerContext) -> float | None:
    """呼叫剩下的秒數；沒有期限為 None。"""
    remaining = context.time_remaining()
    return None if remaining is None or remaining > NO_DEADLINE_SECONDS else remaining
