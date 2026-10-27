"""Job 結束碼契約。K8s 的 podFailurePolicy 與排程器依此分流，數值不可更動。"""

from enum import IntEnum


class ExitCode(IntEnum):
    # 完成，或預算用完而正常停止（剩餘項目留給下次執行）。
    OK = 0
    # 其他暫時性失敗：未預期的例外，重試可能有用。
    FAILED = 1
    # 致命：資料不合預期，重試無用。
    FATAL = 2
    # 斷路器開啟（EX_TEMPFAIL）：端點有問題，稍後再試。
    TEMPFAIL = 75
