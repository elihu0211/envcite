from conftest import FakeClock

from envcite_jobkit import BreakerSnapshot, BreakerState, CircuitBreaker, MemoryBreakerStore


def state(b: CircuitBreaker) -> BreakerState:
    return b.state


def make(clock: FakeClock, **kw: object) -> CircuitBreaker:
    params: dict[str, object] = {
        "failure_threshold": 0.5,
        "min_calls": 4,
        "window_seconds": 60,
        "open_seconds": 30,
        "half_open_probes": 2,
    }
    params.update(kw)
    return CircuitBreaker(clock=clock, **params)  # type: ignore[arg-type]


def test_stays_closed_below_min_calls(clock: FakeClock) -> None:
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    assert state(b) is BreakerState.CLOSED


def test_opens_when_failure_rate_reaches_threshold(clock: FakeClock) -> None:
    b = make(clock)
    b.record_success()
    b.record_success()
    b.record_failure()
    assert state(b) is BreakerState.CLOSED
    b.record_failure()
    assert state(b) is BreakerState.OPEN
    assert not b.allow()


def test_old_outcomes_leave_the_window(clock: FakeClock) -> None:
    b = make(clock)
    for _ in range(3):
        b.record_failure()
    clock.advance(61)
    b.record_failure()
    assert state(b) is BreakerState.CLOSED


def test_half_open_after_open_seconds_then_closes_after_probes(clock: FakeClock) -> None:
    b = make(clock)
    for _ in range(4):
        b.record_failure()
    assert b.retry_after() == 30
    clock.advance(30)
    assert state(b) is BreakerState.HALF_OPEN
    assert b.allow()
    b.record_success()
    assert state(b) is BreakerState.HALF_OPEN
    b.record_success()
    assert state(b) is BreakerState.CLOSED


def test_probe_failure_reopens_and_restarts_timer(clock: FakeClock) -> None:
    b = make(clock)
    for _ in range(4):
        b.record_failure()
    clock.advance(30)
    b.record_success()
    b.record_failure()
    assert state(b) is BreakerState.OPEN
    assert b.retry_after() == 30


def test_closing_resets_window(clock: FakeClock) -> None:
    b = make(clock)
    for _ in range(4):
        b.record_failure()
    clock.advance(30)
    b.record_success()
    b.record_success()
    b.record_failure()
    assert state(b) is BreakerState.CLOSED


def test_state_persists_through_store(clock: FakeClock) -> None:
    store = MemoryBreakerStore()
    b = make(clock, store=store)
    for _ in range(4):
        b.record_failure()
    assert store.snapshot == BreakerSnapshot(BreakerState.OPEN, clock.now)
    clock.advance(10)
    again = make(clock, store=store)
    assert state(again) is BreakerState.OPEN
    assert again.retry_after() == 20
    clock.advance(20)
    assert state(again) is BreakerState.HALF_OPEN


def test_persisted_half_open_is_treated_as_open(clock: FakeClock) -> None:
    store = MemoryBreakerStore(BreakerSnapshot(BreakerState.HALF_OPEN, clock.now - 100))
    assert state(make(clock, store=store)) is BreakerState.HALF_OPEN  # 已過 open_seconds，可再探測
    store = MemoryBreakerStore(BreakerSnapshot(BreakerState.HALF_OPEN, clock.now))
    assert state(make(clock, store=store)) is BreakerState.OPEN
