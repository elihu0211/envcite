from pathlib import Path

import pytest

from envcite_jobkit import Policy, backoff_delay, classify_http_status
from envcite_jobkit.errors import Disposition


def test_defaults_valid() -> None:
    assert Policy().resolved_heartbeat_path == "/tmp/job.heartbeat"


def test_from_env_parses_fields_and_ignores_empty() -> None:
    p = Policy.from_env(
        {
            "JOBKIT_NAME": "sync-moj",
            "JOBKIT_OPEN_SECONDS": "120",
            "JOBKIT_MIN_CALLS": "5",
            "JOBKIT_BUDGET_ITEMS": "1000",
            "JOBKIT_OPEN_ACTION": "wait",
            "JOBKIT_BUDGET_SECONDS": "",
        }
    )
    assert (p.name, p.open_seconds, p.min_calls, p.budget_items) == ("sync-moj", 120, 5, 1000)
    assert p.open_action == "wait" and p.budget_seconds is None
    assert p.resolved_heartbeat_path == "/tmp/sync-moj.heartbeat"


def test_from_env_custom_prefix() -> None:
    assert Policy.from_env({"SYNC_MAX_ATTEMPTS": "7"}, prefix="SYNC_").max_attempts == 7


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValueError):
        Policy.from_env({"JOBKIT_OPEN_ACTION": "later"})
    with pytest.raises(ValueError):
        Policy(failure_threshold=0)
    with pytest.raises(ValueError):
        Policy(max_attempts=0)


def test_file_then_env_precedence(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text('[jobkit]\nopen_seconds = 60\nmin_calls = 3\ndead_letter_table = "dl"\n')
    p = Policy.load(f, {"JOBKIT_MIN_CALLS": "9"})
    assert (p.open_seconds, p.min_calls, p.dead_letter_table) == (60, 9, "dl")


def test_file_unknown_key_rejected(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text("open_secondz = 1\n")
    with pytest.raises(ValueError, match="open_secondz"):
        Policy.from_file(f)


def test_backoff_exponential_capped_with_jitter() -> None:
    kw = {"base": 1.0, "cap": 5.0}
    assert backoff_delay(1, rng=lambda: 1.0, **kw) == 1
    assert backoff_delay(3, rng=lambda: 1.0, **kw) == 4
    assert backoff_delay(10, rng=lambda: 1.0, **kw) == 5
    assert backoff_delay(3, rng=lambda: 0.5, **kw) == 2
    assert backoff_delay(3, rng=lambda: 0.0, **kw) == 0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, None),
        (413, Disposition.ISOLATE),
        (400, Disposition.ISOLATE),
        (408, Disposition.ENDPOINT),
        (429, Disposition.ENDPOINT),
        (500, Disposition.ENDPOINT),
        (503, Disposition.ENDPOINT),
        (401, Disposition.FATAL),
    ],
)
def test_http_status_classification(status: int, expected: Disposition | None) -> None:
    assert classify_http_status(status) is expected
