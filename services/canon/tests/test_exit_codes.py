"""Which failure exits with which code: the one mapping every entry point shares."""

import logging

import psycopg
import pytest

from canon.acquire.engine import DownloadFailed, InvalidSnapshot, NotAnArchive
from canon.exit_codes import Retryable, Unretryable, exit_code, fail
from canon.law.history_sync.report import BatchRejected as HistoryBatchRejected
from canon.law.importer import BatchRejected
from canon.law.judicial_api import AuthRejected, EndpointError
from canon.projection import EmbeddingStopped
from envcite_jobkit import ExitCode

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidSnapshot("x"), ExitCode.FATAL),
        (NotAnArchive("x"), ExitCode.FATAL),
        (BatchRejected("x"), ExitCode.FATAL),
        (HistoryBatchRejected("x"), ExitCode.FATAL),
        (AuthRejected("x"), ExitCode.FATAL),
        (Unretryable("x"), ExitCode.FATAL),
        (EndpointError("x"), ExitCode.FAILED),
        (Retryable("x"), ExitCode.FAILED),
        (EmbeddingStopped(ExitCode.FATAL), ExitCode.FATAL),
        (EmbeddingStopped(ExitCode.TEMPFAIL), ExitCode.TEMPFAIL),
        (EmbeddingStopped(ExitCode.FAILED), ExitCode.FAILED),
        (DownloadFailed("x"), ExitCode.FAILED),
        (psycopg.OperationalError("x"), ExitCode.FAILED),
        (RuntimeError("x"), ExitCode.FAILED),
    ],
)
def test_a_failure_exits_with_the_code_that_says_whether_a_retry_helps(
    error: BaseException, expected: ExitCode
) -> None:
    assert exit_code(error) == expected


def test_a_failed_run_is_rolled_back_logged_and_given_its_code(
    conn: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    conn.execute("CREATE TEMP TABLE kept (x int)")
    conn.commit()
    conn.execute("INSERT INTO kept VALUES (1)")

    with caplog.at_level(logging.ERROR, logger="canon.exit_codes"):
        code = fail(conn, Unretryable("bad batch"), "judgment sync")

    assert code == ExitCode.FATAL
    assert conn.execute("SELECT count(*) FROM kept").fetchone() == (0,)
    assert "judgment sync failed (exit 2): bad batch" in caplog.text


def test_the_exit_codes_keep_the_values_the_deployment_dispatches_on() -> None:
    assert [int(c) for c in (ExitCode.OK, ExitCode.FAILED, ExitCode.FATAL, ExitCode.TEMPFAIL)] == [
        0,
        1,
        2,
        75,
    ]
