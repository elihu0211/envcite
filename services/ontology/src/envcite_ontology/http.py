"""對外 HTTP。憑證放寬與狀態分類只在這裡，呼叫端各自決定重試與禮貌間隔。

這幾站的憑證都缺少 Subject Key Identifier，只清除 `VERIFY_X509_STRICT`；立法院還要舊式
重新協商。狀態碼依 `envcite_jobkit` 分類：連線、逾時、408／429／5xx 是端點問題。
"""

from __future__ import annotations

import http.client
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import cast

from envcite_jobkit import Disposition, classify_http_status


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
