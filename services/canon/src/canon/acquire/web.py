"""循序抓網頁的 HTTP 客戶端：沒有 API、只有網頁的來源共用（公報、主管機關與立法院的法規系統）。

以 `httpx.Client` 共用底層連線池，一次一個請求、請求之間至少隔 `pause` 秒，User-Agent 載明系統
與聯絡處（02-01 §8.1；值見 `canon.net.USER_AGENT`）。失敗依 jobkit 分類：連線、逾時、408／429／5xx
是端點問題，可退避重試；其餘 4xx 隔離該項；401／403 致命。
"""

import hashlib
import time
from pathlib import Path

import httpx

from canon.net import USER_AGENT, tls_context
from envcite_jobkit import EndpointError, ItemError, raise_for_status


class Http:
    """循序、帶禮貌間隔與身分的 HTTP。失敗依 jobkit 分類：端點問題可重試，其餘隔離或致命。"""

    def __init__(self, pause: float, *, legacy_tls: bool = False) -> None:
        """`legacy_tls`：站台不支援安全重新協商（RFC 5746）時才開；憑證照常驗證。"""
        self._pause = pause
        self._last = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            verify=tls_context(legacy=legacy_tls),
            timeout=60.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        """關閉底層連線池。"""
        self._client.close()

    def text(self, url: str) -> str:
        """GET 一頁，回傳解碼後的文字（UTF-8，容忍 BOM）。"""
        return self._request("GET", url).content.decode("utf-8-sig")

    def form(self, url: str, fields: dict[str, str]) -> str:
        """POST 表單（`application/x-www-form-urlencoded`，UTF-8）。"""
        return self._request("POST", url, data=fields).content.decode("utf-8-sig")

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        """GET 並串流存到 `dest`；回傳（SHA-256，位元組數）。回應是空的拋 `ItemError`。"""
        self._throttle()
        h = hashlib.sha256()
        size = 0
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise_for_status(response.status_code, f"{url}: HTTP {response.status_code}")
                with dest.open("wb") as f:
                    for chunk in response.iter_bytes(1 << 20):
                        h.update(chunk)
                        f.write(chunk)
                        size += len(chunk)
        except httpx.HTTPError as e:
            raise EndpointError(f"{url}: {e}") from e
        if size == 0:
            raise ItemError(f"{url}: empty response")
        return h.digest(), size

    def _throttle(self) -> None:
        wait = self._last + self._pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _request(
        self, method: str, url: str, *, data: dict[str, str] | None = None
    ) -> httpx.Response:
        self._throttle()
        try:
            response = self._client.request(method, url, data=data)
        except httpx.HTTPError as e:
            raise EndpointError(f"{url}: {e}") from e
        if response.status_code >= 400:
            raise_for_status(response.status_code, f"{url}: HTTP {response.status_code}")
        return response
