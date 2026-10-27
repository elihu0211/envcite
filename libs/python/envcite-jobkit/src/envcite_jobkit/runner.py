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
    stats = stats or RunStats()
    metrics = metrics or LogMetrics()
    if sink is None:
        sink = (
            JsonlDeadLetterSink(policy.dead_letter_path)
            if policy.dead_letter_path
            else MemoryDeadLetterSink()
        )
    breaker = CircuitBreaker(
        failure_threshold=policy.failure_threshold,
        min_calls=policy.min_calls,
        window_seconds=policy.window_seconds,
        open_seconds=policy.open_seconds,
        half_open_probes=policy.half_open_probes,
        clock=clock,
        store=breaker_store,
    )
    heartbeat = Heartbeat(
        policy.resolved_heartbeat_path,
        min_interval=policy.heartbeat_min_interval_seconds,
        clock=clock,
    )
    jitter = rng if rng is not None else random.random
    started = clock()

    def report() -> None:
        metrics.gauge("jobkit_breaker_state", _BREAKER_GAUGE[breaker.state])
        metrics.gauge("jobkit_dead_letter_total", stats.dead_lettered)
        if policy.budget_seconds is not None:
            metrics.gauge(
                "jobkit_budget_remaining_seconds",
                max(0.0, policy.budget_seconds - (clock() - started)),
            )
        if policy.budget_items is not None:
            metrics.gauge(
                "jobkit_budget_remaining_items", max(0, policy.budget_items - stats.processed)
            )

    def budget_exhausted() -> bool:
        if policy.budget_items is not None and stats.processed >= policy.budget_items:
            return True
        return policy.budget_seconds is not None and clock() - started >= policy.budget_seconds

    def admit() -> None:
        while not breaker.allow():
            if policy.open_action == "exit":
                raise _Stop(ExitCode.TEMPFAIL)
            sleep(breaker.retry_after())
            if budget_exhausted():
                raise _Stop(ExitCode.OK)

    def isolate(item: T, exc: Exception, kind: str, attempts: int) -> None:
        entry = DeadLetter(item_id(item), f"{type(exc).__name__}: {exc}", kind, attempts, clock())
        sink.record(entry)
        stats.dead_lettered += 1
        if policy.max_dead_letters is not None and stats.dead_lettered > policy.max_dead_letters:
            logger.error("隔離數 %d 超過上限 %d", stats.dead_lettered, policy.max_dead_letters)
            raise _Stop(ExitCode.FATAL)

    def process(item: T) -> None:
        attempt = 0
        while True:
            admit()
            attempt += 1
            try:
                handler(item)
            except Exception as exc:
                disposition = classify(exc)
                if disposition is Disposition.ISOLATE:
                    isolate(item, exc, "item", attempt)
                    return
                if disposition is Disposition.FATAL:
                    logger.error("致命錯誤：%s", exc)
                    raise _Stop(ExitCode.FATAL) from exc
                if disposition is Disposition.UNEXPECTED:
                    logger.exception("未分類的例外，Job 以 FAILED 結束")
                    raise _Stop(ExitCode.FAILED) from exc
                breaker.record_failure()
                if breaker.state is BreakerState.OPEN:
                    admit()  # exit 模式在此丟出 TEMPFAIL；wait 模式等到半開再重試同一項
                    continue
                if attempt >= policy.max_attempts:
                    isolate(item, exc, "retries_exhausted", attempt)
                    return
                sleep(
                    backoff_delay(
                        attempt,
                        base=policy.backoff_base_seconds,
                        cap=policy.backoff_max_seconds,
                        rng=jitter,
                    )
                )
            else:
                breaker.record_success()
                stats.succeeded += 1
                return

    code = ExitCode.OK
    try:
        iterator = iter(source)
        while True:
            if budget_exhausted():
                break
            admit()
            try:
                item = next(iterator)
            except StopIteration:
                break
            process(item)
            stats.processed += 1
            heartbeat.beat()
            report()
    except _Stop as stop:
        code = stop.code
    except Exception:
        logger.exception("來源迭代失敗，Job 以 FAILED 結束")
        code = ExitCode.FAILED
    report()
    stats.last_exit = code
    logger.info(
        "job %s 結束：code=%d processed=%d dead_lettered=%d",
        policy.name,
        code,
        stats.processed,
        stats.dead_lettered,
    )
    return code
