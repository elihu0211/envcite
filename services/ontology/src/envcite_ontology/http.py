"""對外 HTTP。憑證放寬、狀態分類與模型端點的 JSON 往返只在這裡，呼叫端各自決定重試與禮貌間隔。

這幾站的憑證都缺少 Subject Key Identifier，只清除 `VERIFY_X509_STRICT`；立法院還要舊式
重新協商。狀態碼依 `envcite_jobkit` 分類：連線、逾時、408／429／5xx 是端點問題。
"""

import http.client
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import cast

from envcite_jobkit import Disposition, classify_http_status

# urllib 預設的 `Python-urllib/x.y` 會被端點前面的 WAF（如 Cloudflare 的 error 1010）擋下，
# 所以明確自報身分；不偽裝成瀏覽器或其他工具。
try:
    USER_AGENT = f"envcite-ontology/{version('envcite-ontology')}"
except PackageNotFoundError:  # 未安裝的原始碼樹
    USER_AGENT = "envcite-ontology"


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


def urlopen(
    url: str,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float,
    context: ssl.SSLContext | None = None,
    method: str | None = None,
) -> http.client.HTTPResponse:
    """成功回傳回應（可讀、可當環境管理器）。4xx／5xx 丟 `HttpStatus`。連線問題原樣拋出。"""
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    try:
        if context is None:
            return cast(http.client.HTTPResponse, urllib.request.urlopen(request, timeout=timeout))
        return cast(
            http.client.HTTPResponse,
            urllib.request.urlopen(request, context=context, timeout=timeout),
        )
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp is not None else b""
        raise HttpStatus(e.code, raw, cast(Mapping[str, str], e.headers)) from e


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
    """POST 一份 JSON，回傳解析後的回應；回應是不是物件由呼叫端判斷。

    4xx／5xx 丟 `HttpStatus`，其餘通訊失敗丟 `TransportError`，重試與例外的分類留給呼叫端。
    """
    try:
        with urlopen(
            url,
            data=json.dumps(payload).encode(),
            headers=json_headers(api_key),
            timeout=timeout,
            method="POST",
        ) as response:
            return json.loads(response.read())
    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ) as e:
        raise TransportError(f"{type(e).__name__}: {e}") from e
