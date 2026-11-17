"""Which failure exits with which code: the one mapping every entry point shares."""

import psycopg
import pytest

from canon.acquire.engine import DownloadFailed, InvalidSnapshot, NotAnArchive
from canon.exit_codes import exit_code
from canon.law.history_sync.report import BatchRejected as HistoryBatchRejected
from canon.law.importer import BatchRejected
from canon.projection import EmbeddingStopped
from envcite_jobkit import ExitCode


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidSnapshot("x"), ExitCode.FATAL),
        (NotAnArchive("x"), ExitCode.FATAL),
        (BatchRejected("x"), ExitCode.FATAL),
        (HistoryBatchRejected("x"), ExitCode.FATAL),
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


def test_the_exit_codes_keep_the_values_the_deployment_dispatches_on() -> None:
    assert [int(c) for c in (ExitCode.OK, ExitCode.FAILED, ExitCode.FATAL, ExitCode.TEMPFAIL)] == [
        0,
        1,
        2,
        75,
    ]
