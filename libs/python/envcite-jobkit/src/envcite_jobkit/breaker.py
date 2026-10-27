"""自寫的小型斷路器：closed → open → half-open → closed。

非執行緒安全；批次 Job 一次處理一項。時鐘可注入。預設 `time.time`（掛鐘時間）而非
單調時鐘，因為持久化的 open 狀態要跨程序比較時間。
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerSnapshot:
    state: BreakerState
    opened_at: float


class BreakerStore(Protocol):
    """跨次執行的斷路器狀態。每個斷路器名稱一列（見 README 的資料表設計）。"""

    def load(self) -> BreakerSnapshot | None: ...

    def save(self, snapshot: BreakerSnapshot) -> None: ...


class MemoryBreakerStore:
    def __init__(self, snapshot: BreakerSnapshot | None = None) -> None:
        self.snapshot = snapshot

    def load(self) -> BreakerSnapshot | None:
        return self.snapshot

    def save(self, snapshot: BreakerSnapshot) -> None:
        self.snapshot = snapshot


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: float = 0.5,
        min_calls: int = 10,
        window_seconds: float = 60.0,
        open_seconds: float = 300.0,
        half_open_probes: int = 3,
        clock: Callable[[], float] = time.time,
        store: BreakerStore | None = None,
    ) -> None:
        self._threshold = failure_threshold
        self._min_calls = min_calls
        self._window = window_seconds
        self._open_seconds = open_seconds
        self._probes = half_open_probes
        self._clock = clock
        self._store = store
        self._outcomes: deque[tuple[float, bool]] = deque()
        self._probe_successes = 0
        self._state = BreakerState.CLOSED
        self._opened_at = 0.0
        saved = store.load() if store else None
        if saved is not None and saved.state is not BreakerState.CLOSED:
            # 半開中途結束的程序，視同仍開啟：重新計時再探測。
            self._state = BreakerState.OPEN
            self._opened_at = saved.opened_at

    @property
    def state(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self._clock() - self._opened_at >= self._open_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._probe_successes = 0
            self._persist()
        return self._state

    def allow(self) -> bool:
        return self.state is not BreakerState.OPEN

    def retry_after(self) -> float:
        """開啟狀態下距離半開還要幾秒；其他狀態為 0。"""
        if self.state is not BreakerState.OPEN:
            return 0.0
        return max(0.0, self._opened_at + self._open_seconds - self._clock())

    def record_success(self) -> None:
        state = self.state
        if state is BreakerState.HALF_OPEN:
            self._probe_successes += 1
            if self._probe_successes >= self._probes:
                self._state = BreakerState.CLOSED
                self._outcomes.clear()
                self._persist()
        elif state is BreakerState.CLOSED:
            self._observe(True)

    def record_failure(self) -> None:
        state = self.state
        if state is BreakerState.HALF_OPEN:
            self._trip()
        elif state is BreakerState.CLOSED:
            self._observe(False)
            calls = len(self._outcomes)
            failures = sum(1 for _, ok in self._outcomes if not ok)
            if calls >= self._min_calls and failures / calls >= self._threshold:
                self._trip()

    def _observe(self, ok: bool) -> None:
        now = self._clock()
        self._outcomes.append((now, ok))
        while self._outcomes and now - self._outcomes[0][0] > self._window:
            self._outcomes.popleft()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._outcomes.clear()
        self._persist()

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(BreakerSnapshot(self._state, self._opened_at))
