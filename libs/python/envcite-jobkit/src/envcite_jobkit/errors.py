"""處理函式用例外型別表達「這次失敗該怎麼處置」，斷路判斷需要的領域知識留在呼叫端。"""

from enum import Enum, auto


class Disposition(Enum):
    # 單項問題（如過大、壞資料）：隔離該項、繼續，不計入斷路器。
    ISOLATE = auto()
    # 端點問題（逾時、連線、限流、5xx）：計入斷路器，可重試。
    ENDPOINT = auto()
    # 資料不合預期，重試無用：整個 Job 以 FATAL 結束。
    FATAL = auto()
    # 未分類：不吞掉，Job 以 FAILED 結束。
    UNEXPECTED = auto()


class JobError(Exception):
    disposition: Disposition = Disposition.UNEXPECTED


class ItemError(JobError):
    disposition = Disposition.ISOLATE


class EndpointError(JobError):
    disposition = Disposition.ENDPOINT


class FatalError(JobError):
    disposition = Disposition.FATAL


def classify_http_status(status: int) -> Disposition | None:
    """HTTP 狀態碼的預設分類；2xx／3xx 回傳 None（不是失敗）。

    413 與其他 4xx 針對的是這一項的內容，重試同樣內容沒用，隔離；408／429／5xx 是端點的
    狀況，計入斷路器。401／403 是設定問題，整個 Job 都會失敗，視為致命。
    """
    if status < 400:
        return None
    if status in (401, 403):
        return Disposition.FATAL
    if status in (408, 429) or status >= 500:
        return Disposition.ENDPOINT
    return Disposition.ISOLATE


def raise_for_status(status: int, message: str = "") -> None:
    """依 `classify_http_status` 丟出對應的 JobError；成功時不做事。"""
    disposition = classify_http_status(status)
    if disposition is None:
        return
    text = message or f"HTTP {status}"
    cls = {
        Disposition.ISOLATE: ItemError,
        Disposition.ENDPOINT: EndpointError,
        Disposition.FATAL: FatalError,
    }[disposition]
    raise cls(text)


def default_classify(exc: Exception) -> Disposition:
    """JobError 用自己的分類；標準庫的逾時與連線錯誤屬端點問題；其餘為未分類。"""
    if isinstance(exc, JobError):
        return exc.disposition
    if isinstance(exc, TimeoutError | ConnectionError):
        return Disposition.ENDPOINT
    return Disposition.UNEXPECTED
