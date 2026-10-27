"""進度心跳：每完成一項更新檔案的 mtime，供 livenessProbe 以「多久沒進度」判斷卡住。"""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path


class Heartbeat:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        min_interval: float = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self._min_interval = min_interval
        self._clock = clock
        self._last: float | None = None

    def beat(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            return
        self.path.touch()
        os.utime(self.path, (now, now))
        self._last = now

    def age(self) -> float | None:
        """距上次心跳的秒數；檔案不存在為 None。"""
        try:
            return self._clock() - self.path.stat().st_mtime
        except FileNotFoundError:
            return None


def main(argv: list[str] | None = None) -> int:
    """`python -m envcite_jobkit.heartbeat <path> <max_age_seconds>`：新鮮回 0，否則 1。

    檔案尚不存在（Job 剛起、第一項還沒完成）視為健康；寬限由 startupProbe／initialDelay 負責。
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: heartbeat <path> <max_age_seconds>", file=sys.stderr)
        return 2
    age = Heartbeat(args[0]).age()
    return 0 if age is None or age <= float(args[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
