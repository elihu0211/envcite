"""入口的結束碼：哪種失敗以哪個碼結束的單一對應。

數值是與部署庫的契約（`podFailurePolicy` 依它分流，定義在 `envcite_jobkit.ExitCode`），這裡不
重新定義；各入口只決定「這個失敗重試有沒有用」，不寫數字。例外自己帶碼，所以新增一種失敗只是
新增一個例外類別，不必回頭改這個對應。
"""

from envcite_jobkit import ExitCode


class JobStopped(Exception):
    """入口該以 `code` 結束的失敗；沒有繼承它的例外一律視為暫時性失敗。"""

    code: ExitCode = ExitCode.FAILED


class Unretryable(JobStopped):
    """資料不合預期，重試不會改變結果：以 `ExitCode.FATAL` 結束，部署據此直接判定失敗。"""

    code = ExitCode.FATAL


def exit_code(error: BaseException) -> ExitCode:
    """例外 → 結束碼：自帶碼的用它的碼，其餘為暫時性失敗（可重試）。"""
    return error.code if isinstance(error, JobStopped) else ExitCode.FAILED
