"""呼叫期限的讀取與往下游傳遞。

gRPC Python 不自動傳播期限：下游呼叫要自己帶上游剩下的秒數，否則上游早已逾時，
下游還在白做工。
"""

from __future__ import annotations

import grpc

#: 沒有期限時，`time_remaining()` 回的是一個極大的數而不是 None；超過一天都當作沒有。
NO_DEADLINE_SECONDS = 86400.0


def remaining_seconds(context: grpc.ServicerContext) -> float | None:
    """呼叫剩下的秒數；沒有期限為 None。"""
    remaining = context.time_remaining()
    return None if remaining is None or remaining > NO_DEADLINE_SECONDS else remaining


def downstream_timeout(own: float, remaining: float | None) -> float:
    """下游呼叫的逾時：自己的上限與上游剩餘的秒數取小者。"""
    return own if remaining is None else min(own, remaining)
