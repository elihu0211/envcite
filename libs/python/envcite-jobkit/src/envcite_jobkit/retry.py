"""退避加抖動（full jitter）。"""

import random
from collections.abc import Callable


def backoff_delay(
    attempt: int,
    *,
    base: float,
    cap: float,
    rng: Callable[[], float] = random.random,
) -> float:
    """第 `attempt` 次失敗（從 1 起算）後的等待秒數：[0, min(cap, base * 2^(attempt-1))]。"""
    ceiling = min(cap, base * float(2 ** (attempt - 1)))
    return ceiling * rng()
