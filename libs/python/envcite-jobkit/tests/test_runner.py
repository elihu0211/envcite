import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock

from envcite_jobkit import (
    BreakerSnapshot,
    BreakerState,
    DeadLetter,
    EndpointError,
    ExitCode,
    FatalError,
    ItemError,
    JsonlDeadLetterSink,
    MemoryBreakerStore,
    MemoryDeadLetterSink,
    Metrics,
    NullMetrics,
    Policy,
    RunStats,
    raise_for_status,
    run,
)


class Gauges:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}

    def gauge(self, name: str, value: float) -> None:
        self.values[name] = value


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    return Policy(
        name="t",
        min_calls=4,
        max_attempts=2,
        open_seconds=30,
        half_open_probes=1,
        heartbeat_path=str(tmp_path / "hb"),
    )


def call[T](
    clock: FakeClock,
    source: Iterable[T],
    handler: Callable[[T], object],
    policy: Policy,
    *,
    metrics: Metrics | None = None,
    **kw: Any,
) -> ExitCode:
    return run(
        source,
        handler,
        policy,
        metrics=metrics or NullMetrics(),
        clock=clock,
        sleep=clock.sleep,
        rng=lambda: 1.0,
        **kw,
    )


def test_completes_with_ok_and_beats_heartbeat(clock: FakeClock, policy: Policy) -> None:
    seen: list[int] = []
    stats = RunStats()
    assert call(clock, range(3), seen.append, policy, stats=stats) == ExitCode.OK
    assert seen == [0, 1, 2]
    assert (stats.processed, stats.succeeded, stats.dead_lettered) == (3, 3, 0)
    assert Path(policy.resolved_heartbeat_path).stat().st_mtime == clock.now


def test_heartbeat_advances_per_item(clock: FakeClock, policy: Policy) -> None:
    mtimes: list[float] = []

    def handler(_: int) -> None:
        clock.advance(10)

    def source() -> Iterator[int]:
        for i in range(3):
            if i:
                mtimes.append(Path(policy.resolved_heartbeat_path).stat().st_mtime)
            yield i

    call(clock, source(), handler, policy)
    assert mtimes == [1_010, 1_020]


def test_item_error_is_isolated_and_does_not_count_for_breaker(
    clock: FakeClock, policy: Policy
) -> None:
    sink = MemoryDeadLetterSink()
    store = MemoryBreakerStore()

    def handler(n: int) -> None:
        if n % 2 == 0:
            raise_for_status(413, "payload too large")

    # 全部是 413 也不開斷路器
    code = call(clock, range(20), handler, policy, sink=sink, breaker_store=store)
    assert code == ExitCode.OK
    assert len(sink.entries) == 10
    first = sink.entries[0]
    assert (first.item_id, first.kind, first.attempts) == ("0", "item", 1)
    assert "ItemError" in first.error
    assert store.snapshot is None


def test_all_items_413_never_trips_breaker(clock: FakeClock, policy: Policy) -> None:
    def handler(_: int) -> None:
        raise ItemError("413")

    stats = RunStats()
    assert call(clock, range(50), handler, policy, stats=stats) == ExitCode.OK
    assert stats.dead_lettered == 50


def test_endpoint_errors_open_breaker_and_exit_tempfail(clock: FakeClock, policy: Policy) -> None:
    store = MemoryBreakerStore()
    handled: list[int] = []

    def handler(n: int) -> None:
        handled.append(n)
        raise_for_status(503)

    code = call(clock, range(100), handler, policy, breaker_store=store)
    assert code == ExitCode.TEMPFAIL
    assert store.snapshot is not None and store.snapshot.state is BreakerState.OPEN
    assert len(handled) == 4  # min_calls 次失敗後立刻停，不耗盡來源


def test_open_persisted_breaker_exits_before_consuming_source(
    clock: FakeClock, policy: Policy
) -> None:
    store = MemoryBreakerStore(BreakerSnapshot(BreakerState.OPEN, clock.now - 5))
    consumed: list[int] = []

    def source() -> Iterator[int]:
        for i in range(3):
            consumed.append(i)
            yield i

    assert call(clock, source(), lambda _: None, policy, breaker_store=store) == ExitCode.TEMPFAIL
    assert consumed == []


def test_retry_with_backoff_then_success(clock: FakeClock, policy: Policy) -> None:
    attempts: list[int] = []

    def handler(n: int) -> None:
        attempts.append(n)
        if len(attempts) == 1:
            raise TimeoutError

    stats = RunStats()
    assert call(clock, [7], handler, policy, stats=stats) == ExitCode.OK
    assert attempts == [7, 7]
    assert clock.sleeps == [0.5]  # base * 2^0 * rng(=1.0)
    assert stats.succeeded == 1 and stats.dead_lettered == 0


def test_retries_exhausted_isolates_item_when_breaker_stays_closed(
    clock: FakeClock, policy: Policy
) -> None:
    sink = MemoryDeadLetterSink()

    def handler(n: int) -> None:
        if n == 0:
            raise EndpointError("slow")

    code = call(clock, range(30), handler, policy, sink=sink)
    assert code == ExitCode.OK
    assert [(e.item_id, e.kind, e.attempts) for e in sink.entries] == [
        ("0", "retries_exhausted", 2)
    ]


def test_budget_items_exhausted_is_ok_and_does_not_consume_more(
    clock: FakeClock, policy: Policy
) -> None:
    policy = Policy(**{**policy.__dict__, "budget_items": 2})
    consumed: list[int] = []

    def source() -> Iterator[int]:
        for i in range(10):
            consumed.append(i)
            yield i

    gauges = Gauges()
    assert call(clock, source(), lambda _: None, policy, metrics=gauges) == ExitCode.OK
    assert consumed == [0, 1]
    assert gauges.values["jobkit_budget_remaining_items"] == 0


def test_budget_seconds_exhausted_is_ok(clock: FakeClock, policy: Policy) -> None:
    policy = Policy(**{**policy.__dict__, "budget_seconds": 25})
    handled: list[int] = []

    def handler(n: int) -> None:
        handled.append(n)
        clock.advance(10)

    gauges = Gauges()
    assert call(clock, range(100), handler, policy, metrics=gauges) == ExitCode.OK
    assert handled == [0, 1, 2]
    assert gauges.values["jobkit_budget_remaining_seconds"] == 0


def test_fatal_error_exits_2(clock: FakeClock, policy: Policy) -> None:
    def handler(_: int) -> None:
        raise FatalError("schema mismatch")

    assert call(clock, range(3), handler, policy) == ExitCode.FATAL


def test_auth_status_is_fatal(clock: FakeClock, policy: Policy) -> None:
    assert call(clock, [1], lambda _: raise_for_status(401), policy) == ExitCode.FATAL


def test_unexpected_exception_exits_1(clock: FakeClock, policy: Policy) -> None:
    def handler(_: int) -> None:
        raise KeyError("bug")

    assert call(clock, range(3), handler, policy) == ExitCode.FAILED


def test_source_failure_exits_1(clock: FakeClock, policy: Policy) -> None:
    def source() -> Iterator[int]:
        yield 1
        raise RuntimeError("db down")

    assert call(clock, source(), lambda _: None, policy) == ExitCode.FAILED


def test_too_many_dead_letters_is_fatal(clock: FakeClock, policy: Policy) -> None:
    policy = Policy(**{**policy.__dict__, "max_dead_letters": 2})

    def handler(_: int) -> None:
        raise ItemError("bad")

    stats = RunStats()
    assert call(clock, range(10), handler, policy, stats=stats) == ExitCode.FATAL
    assert stats.dead_lettered == 3


def test_open_action_wait_sleeps_until_half_open_then_recovers(
    clock: FakeClock, policy: Policy
) -> None:
    policy = Policy(**{**policy.__dict__, "open_action": "wait", "max_attempts": 10})
    calls = {"n": 0}

    def handler(_: int) -> None:
        calls["n"] += 1
        if calls["n"] <= 4:
            raise EndpointError("down")

    stats = RunStats()
    assert call(clock, [1], handler, policy, stats=stats) == ExitCode.OK
    assert 30 in clock.sleeps  # 等滿 open_seconds
    assert stats.succeeded == 1


def test_open_action_wait_stops_with_ok_when_the_budget_runs_out_while_waiting(
    clock: FakeClock, policy: Policy
) -> None:
    policy = Policy(
        **{**policy.__dict__, "open_action": "wait", "max_attempts": 10, "budget_seconds": 10}
    )
    calls: list[int] = []

    def handler(n: int) -> None:
        calls.append(n)
        raise EndpointError("down")

    stats = RunStats()
    assert call(clock, [1], handler, policy, stats=stats) == ExitCode.OK
    assert len(calls) == policy.min_calls
    assert clock.sleeps[-1] == policy.open_seconds  # 等到一半預算就用完，不等到半開
    assert (stats.processed, stats.succeeded, stats.dead_lettered) == (0, 0, 0)


def test_item_id_and_jsonl_sink(clock: FakeClock, policy: Policy, tmp_path: Path) -> None:
    path = tmp_path / "dl.jsonl"

    def handler(_: dict[str, str]) -> None:
        raise ItemError("太大")

    call(
        clock,
        [{"id": "a-1"}],
        handler,
        policy,
        item_id=lambda i: i["id"],
        sink=JsonlDeadLetterSink(path),
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["item_id"] == "a-1" and row["kind"] == "item" and "太大" in row["error"]


def test_metrics_report_breaker_state_and_dead_letters(clock: FakeClock, policy: Policy) -> None:
    gauges = Gauges()
    call(clock, range(2), lambda _: raise_for_status(400), policy, metrics=gauges)
    assert gauges.values["jobkit_breaker_state"] == 0
    assert gauges.values["jobkit_dead_letter_total"] == 2
    call(clock, range(9), lambda _: raise_for_status(500), policy, metrics=gauges)
    assert gauges.values["jobkit_breaker_state"] == 2


def test_memory_sink_records_entries() -> None:
    sink = MemoryDeadLetterSink()
    sink.record(DeadLetter("x", "e", "item", 1))
    assert sink.entries[0].item_id == "x"
