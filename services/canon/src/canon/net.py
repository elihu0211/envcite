"""對外 HTTP。憑證放寬、狀態分類與模型端點的 JSON 往返只在這裡，呼叫端各自決定重試與禮貌間隔。

這幾站的憑證都缺少 Subject Key Identifier，只清除 `VERIFY_X509_STRICT`；立法院還要舊式
重新協商。狀態碼依 `envcite_jobkit` 分類：連線、逾時、408／429／5xx 是端點問題。
"""

import ssl
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, metadata

import httpx

from envcite_jobkit import Disposition, classify_http_status


def _user_agent() -> str:
    """`canon/<版本> (+<聯絡處>)`；版本與聯絡處取自套件中繼資料。"""
    try:
        meta = metadata("canon")
    except PackageNotFoundError:  # 未安裝的原始碼樹
        return "canon"
    agent = f"canon/{meta['Version']}"
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(", ")
        if label == "Repository":
            return f"{agent} (+{url})"
    return agent


#: 對外請求的 User-Agent。urllib／httpx 預設值會被端點前面的 WAF（如 Cloudflare 的 error 1010）
#: 擋下，所以明確自報身分；不偽裝成瀏覽器或其他工具。聯絡處讓沒有 API 的來源站台知道找誰。
USER_AGENT = _user_agent()


class HttpStatus(Exception):
    """HTTP 4xx／5xx。`disposition` 是 jobkit 的分類，本文最多讀一次。"""

    def __init__(self, status: int, body: bytes, headers: Mapping[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = headers
        self.disposition = classify_http_status(status)
        super().__init__(f"HTTP {status}")


def tls_context(*, legacy: bool = False) -> ssl.SSLContext:
    """預設驗證，只放寬缺少 Subject Key Identifier 的站。`legacy` 給不支援 RFC 5746 的站。"""
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    if legacy:
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return context


def endpoint(status: HttpStatus) -> bool:
    """這個狀態值得退避重試。"""
    return status.disposition is Disposition.ENDPOINT


class TransportError(Exception):
    """請求沒有得到可讀的回應：連線失敗、逾時、協定錯誤，或回應不是 JSON。"""


def json_headers(api_key: str | None) -> dict[str, str]:
    """JSON 請求的標頭；有金鑰才帶 `Authorization`。"""
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def post_json(url: str, payload: object, *, api_key: str | None, timeout: float) -> object:
    """以 `httpx` POST 一份 JSON，回傳解析後的回應；回應是不是物件由呼叫端判斷。

    4xx／5xx 丟 `HttpStatus`，其餘通訊失敗丟 `TransportError`，重試與例外的分類留給呼叫端。
    """
    try:
        response = httpx.post(url, json=payload, headers=json_headers(api_key), timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HttpStatus(e.response.status_code, e.response.content, e.response.headers) from e
    except (httpx.HTTPError, ValueError) as e:
        raise TransportError(f"{type(e).__name__}: {e}") from e
