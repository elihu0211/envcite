"""`run`：把政策套在一串工作項上，回傳結束碼。"""

import logging
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .breaker import BreakerState, BreakerStore, CircuitBreaker
from .deadletter import DeadLetter, DeadLetterSink, JsonlDeadLetterSink, MemoryDeadLetterSink
from .errors import Disposition, default_classify
from .exitcodes import ExitCode
from .heartbeat import Heartbeat
from .metrics import LogMetrics, Metrics
from .policy import Policy
from .retry import backoff_delay

logger = logging.getLogger("envcite_jobkit")

_BREAKER_GAUGE = {BreakerState.CLOSED: 0, BreakerState.HALF_OPEN: 1, BreakerState.OPEN: 2}


class _Stop(Exception):
    def __init__(self, code: ExitCode) -> None:
        self.code = code


@dataclass
class RunStats:
    processed: int = 0
    succeeded: int = 0
    dead_lettered: int = 0
    last_exit: ExitCode | None = field(default=None, repr=False)


class _Run[T]:
    """一次 `run` 的狀態：政策、斷路器、心跳、隔離去處與計數；方法各管一件事。"""

    def __init__(
        self,
        handler: Callable[[T], object],
        policy: Policy,
        *,
        item_id: Callable[[T], str],
        classify: Callable[[Exception], Disposition],
        sink: DeadLetterSink,
        breaker: CircuitBreaker,
        heartbeat: Heartbeat,
        metrics: Metrics,
        stats: RunStats,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        jitter: Callable[[], float],
    ) -> None:
        self._handler = handler
        self._policy = policy
        self._item_id = item_id
        self._classify = classify
        self._sink = sink
        self._breaker = breaker
        self._heartbeat = heartbeat
        self._metrics = metrics
        self._stats = stats
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._started = clock()

    def execute(self, source: Iterable[T]) -> ExitCode:
        """逐項處理到來源用完、預算用完或被停下；回傳結束碼並記入 `stats.last_exit`。"""
        code = ExitCode.OK
        try:
            iterator = iter(source)
            while True:
                if self._budget_exhausted():
                    break
                self._admit()
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                self._process(item)
                self._stats.processed += 1
                self._heartbeat.beat()
                self._report()
        except _Stop as stop:
            code = stop.code
        except Exception:
            logger.exception("來源迭代失敗，Job 以 FAILED 結束")
            code = ExitCode.FAILED
        self._report()
        self._stats.last_exit = code
        logger.info(
            "job %s 結束：code=%d processed=%d dead_lettered=%d",
            self._policy.name,
            code,
            self._stats.processed,
            self._stats.dead_lettered,
        )
        return code

    def _report(self) -> None:
        policy, stats = self._policy, self._stats
        self._metrics.gauge("jobkit_breaker_state", _BREAKER_GAUGE[self._breaker.state])
        self._metrics.gauge("jobkit_dead_letter_total", stats.dead_lettered)
        if policy.budget_seconds is not None:
            self._metrics.gauge(
                "jobkit_budget_remaining_seconds",
                max(0.0, policy.budget_seconds - (self._clock() - self._started)),
            )
        if policy.budget_items is not None:
            self._metrics.gauge(
                "jobkit_budget_remaining_items", max(0, policy.budget_items - stats.processed)
            )

    def _budget_exhausted(self) -> bool:
        policy = self._policy
        if policy.budget_items is not None and self._stats.processed >= policy.budget_items:
            return True
        return (
            policy.budget_seconds is not None
            and self._clock() - self._started >= policy.budget_seconds
        )

    def _admit(self) -> None:
        """斷路器放行才往下；`exit` 模式在開啟時停下（TEMPFAIL），`wait` 模式等到半開。"""
        while not self._breaker.allow():
            if self._policy.open_action == "exit":
                raise _Stop(ExitCode.TEMPFAIL)
            self._sleep(self._breaker.retry_after())
            if self._budget_exhausted():
                raise _Stop(ExitCode.OK)

    def _isolate(self, item: T, exc: Exception, kind: str, attempts: int) -> None:
        policy, stats = self._policy, self._stats
        entry = DeadLetter(
            self._item_id(item), f"{type(exc).__name__}: {exc}", kind, attempts, self._clock()
        )
        self._sink.record(entry)
        stats.dead_lettered += 1
        if policy.max_dead_letters is not None and stats.dead_lettered > policy.max_dead_letters:
            logger.error("隔離數 %d 超過上限 %d", stats.dead_lettered, policy.max_dead_letters)
            raise _Stop(ExitCode.FATAL)

    def _process(self, item: T) -> None:
        """對一項呼叫 `handler`，失敗依處置：隔離、停下，或計入斷路器並退避重試。"""
        attempt = 0
        while True:
            self._admit()
            attempt += 1
            try:
                self._handler(item)
            except Exception as exc:
                if self._on_failure(item, exc, attempt):
                    return
            else:
                self._breaker.record_success()
                self._stats.succeeded += 1
                return

    def _on_failure(self, item: T, exc: Exception, attempt: int) -> bool:
        """處理一次失敗；這一項已了結（隔離）回 True，要再試一次回 False。"""
        disposition = self._classify(exc)
        if disposition is Disposition.ISOLATE:
            self._isolate(item, exc, "item", attempt)
            return True
        if disposition is Disposition.FATAL:
            logger.error("致命錯誤：%s", exc)
            raise _Stop(ExitCode.FATAL) from exc
        if disposition is Disposition.UNEXPECTED:
            logger.exception("未分類的例外，Job 以 FAILED 結束")
            raise _Stop(ExitCode.FAILED) from exc
        self._breaker.record_failure()
        if self._breaker.state is BreakerState.OPEN:
            self._admit()  # exit 模式在此丟出 TEMPFAIL；wait 模式等到半開再重試同一項
            return False
        if attempt >= self._policy.max_attempts:
            self._isolate(item, exc, "retries_exhausted", attempt)
            return True
        self._sleep(
            backoff_delay(
                attempt,
                base=self._policy.backoff_base_seconds,
                cap=self._policy.backoff_max_seconds,
                rng=self._jitter,
            )
        )
        return False


def run[T](
    source: Iterable[T],
    handler: Callable[[T], object],
    policy: Policy | None = None,
    *,
    item_id: Callable[[T], str] = str,
    classify: Callable[[Exception], Disposition] = default_classify,
    sink: DeadLetterSink | None = None,
    breaker_store: BreakerStore | None = None,
    metrics: Metrics | None = None,
    stats: RunStats | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] | None = None,
) -> ExitCode:
    """逐項呼叫 `handler`。處置依 `classify`：

    - ISOLATE：記入隔離、不計入斷路器、繼續。
    - ENDPOINT：計入斷路器並依退避重試；斷路器開啟即停（`open_action="exit"` 時回 TEMPFAIL）。
      重試用盡但斷路器未開，該項記入隔離。
    - FATAL：回 FATAL。UNEXPECTED：記錄後回 FAILED（不吞例外）。

    預算在取下一項之前檢查，用完回 OK，來源不會多消耗一項。
    """
    policy = policy or Policy()
    if sink is None:
        sink = (
            JsonlDeadLetterSink(policy.dead_letter_path)
            if policy.dead_letter_path
            else MemoryDeadLetterSink()
        )
    return _Run(
        handler,
        policy,
        item_id=item_id,
        classify=classify,
        sink=sink,
        breaker=CircuitBreaker(
            failure_threshold=policy.failure_threshold,
            min_calls=policy.min_calls,
            window_seconds=policy.window_seconds,
            open_seconds=policy.open_seconds,
            half_open_probes=policy.half_open_probes,
            clock=clock,
            store=breaker_store,
        ),
        heartbeat=Heartbeat(
            policy.resolved_heartbeat_path,
            min_interval=policy.heartbeat_min_interval_seconds,
            clock=clock,
        ),
        metrics=metrics or LogMetrics(),
        stats=stats or RunStats(),
        clock=clock,
        sleep=sleep,
        jitter=rng if rng is not None else random.random,
    ).execute(source)
