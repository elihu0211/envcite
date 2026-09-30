"""Observability signals (03-04 §7): each one fires on its condition and only then."""

import json
from datetime import timedelta

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]


def _fires(conn: Conn, subject: str, signal: str) -> bool | None:
    """Whether the signal fires for the subject; None when the view has no such row."""
    row = conn.execute(
        "SELECT firing FROM source.signal WHERE subject = %s AND signal = %s", (subject, signal)
    ).fetchone()
    return None if row is None else bool(row[0])


def _run(
    conn: Conn,
    *,
    ago: timedelta,
    outcome: str = "success",
    exit_code: int = 0,
    versioned: int = 0,
    corrected: int = 0,
    ratios: dict[str, dict[str, float]] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO source.run (source, started_at, finished_at, outcome, exit_code, step,"
        " laws_versioned, laws_corrected, ratios)"
        " VALUES ('moj', now() - %(ago)s - interval '5 minutes', now() - %(ago)s, %(outcome)s,"
        "         %(code)s, 'projection', %(v)s, %(c)s, %(ratios)s)",
        {
            "ago": ago,
            "outcome": outcome,
            "code": exit_code,
            "v": versioned,
            "c": corrected,
            "ratios": json.dumps(ratios) if ratios is not None else None,
        },
    )


def _snapshot(conn: Conn, *, ago: timedelta, sha: int = 1) -> int:
    row = conn.execute(
        "INSERT INTO source.snapshot"
        " (feed, data_date, fetched_at, sha256, size, location, attribution)"
        " VALUES ('moj_law', '2026-09-18', now() - %s, %s, 1, %s, 'x') RETURNING id",
        (ago, sha.to_bytes(32), f"moj_law/{sha}"),
    ).fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def test_a_failed_latest_run_fires_until_a_later_run_succeeds(conn: Conn) -> None:
    _run(conn, ago=timedelta(hours=3), outcome="failed", exit_code=2)
    assert _fires(conn, "moj", "last_run_failed") is True
    _run(conn, ago=timedelta(hours=1))
    assert _fires(conn, "moj", "last_run_failed") is False


def test_success_goes_stale_after_36_hours(conn: Conn) -> None:
    _run(conn, ago=timedelta(hours=2), outcome="failed", exit_code=1)
    assert _fires(conn, "moj", "success_stale") is True  # 從未成功
    _run(conn, ago=timedelta(hours=40), outcome="unchanged")
    assert _fires(conn, "moj", "success_stale") is True
    _run(conn, ago=timedelta(hours=30), outcome="success")
    assert _fires(conn, "moj", "success_stale") is False


def test_a_feed_without_a_new_snapshot_for_14_days_is_stalled(conn: Conn) -> None:
    _snapshot(conn, ago=timedelta(days=15))
    assert _fires(conn, "moj_law", "source_stalled") is True
    _snapshot(conn, ago=timedelta(days=1), sha=2)
    assert _fires(conn, "moj_law", "source_stalled") is False


def test_a_ratio_at_80_percent_of_its_limit_fires(conn: Conn) -> None:
    _run(conn, ago=timedelta(hours=1), ratios={"unknown_events": {"value": 0.03, "limit": 0.05}})
    assert _fires(conn, "moj", "ratio_near_limit") is False
    _run(
        conn,
        ago=timedelta(minutes=10),
        ratios={
            "unknown_events": {"value": 0.03, "limit": 0.05},
            "degraded_articles": {"value": 0.0081, "limit": 0.01},
        },
    )
    assert _fires(conn, "moj", "ratio_near_limit") is True
    detail = conn.execute(
        "SELECT detail FROM source.signal WHERE signal = 'ratio_near_limit'"
    ).fetchone()
    assert detail is not None and "degraded_articles 0.0081/0.01" in str(detail[0])


def test_a_run_that_touches_more_than_one_percent_of_the_laws_fires(conn: Conn) -> None:
    total = conn.execute("SELECT count(*) FROM corpus.legal_resource").fetchone()
    assert total is not None and isinstance(total[0], int)
    over = total[0] // 100 + 1
    _run(conn, ago=timedelta(hours=1), versioned=over - 1, corrected=1)
    assert _fires(conn, "moj", "version_surge") is True
    _run(conn, ago=timedelta(minutes=10), versioned=0, corrected=0)
    assert _fires(conn, "moj", "version_surge") is False


def test_a_correction_left_unreviewed_for_a_week_fires_until_it_is_deleted(conn: Conn) -> None:
    old = _snapshot(conn, ago=timedelta(days=8))
    item = conn.execute(
        "INSERT INTO source.item (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
        " VALUES ('moj_law', 'T0000001', %s, %s, %s) RETURNING id",
        (bytes(32), old, old),
    ).fetchone()
    assert item is not None
    conn.execute(
        "INSERT INTO source.import_problem (item_id, snapshot_id, level, code, detail)"
        " VALUES (%s, %s, 'warning', 'corrected', '1 個單元的文字改變')",
        (item[0], old),
    )
    assert _fires(conn, "moj_law", "review_backlog") is True

    conn.execute("DELETE FROM source.import_problem WHERE item_id = %s", (item[0],))
    assert _fires(conn, "moj_law", "review_backlog") is False


def test_a_recent_unreviewed_problem_does_not_fire_yet(conn: Conn) -> None:
    recent = _snapshot(conn, ago=timedelta(days=2))
    item = conn.execute(
        "INSERT INTO source.item (feed, key, content_sha256, first_snapshot_id, last_snapshot_id)"
        " VALUES ('moj_law', 'T0000001', %s, %s, %s) RETURNING id",
        (bytes(32), recent, recent),
    ).fetchone()
    assert item is not None
    conn.execute(
        "INSERT INTO source.import_problem (item_id, snapshot_id, level, code, detail)"
        " VALUES (%s, %s, 'error', 'version_regressed', '')",
        (item[0], recent),
    )
    assert _fires(conn, "moj_law", "review_backlog") is False
