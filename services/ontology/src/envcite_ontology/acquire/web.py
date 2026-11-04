"""循序抓網頁的 HTTP 客戶端：沒有 API、只有網頁的來源共用（公報、主管機關與立法院的法規系統）。

一次一個請求、請求之間至少隔 `pause` 秒，User-Agent 載明系統與聯絡處（02-01 §8.1）。失敗依 jobkit
分類：連線、逾時、408／429／5xx 是端點問題，可退避重試；其餘 4xx 隔離該項；401／403 致命。
"""

from __future__ import annotations

import hashlib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from pathlib import Path
from typing import Any, cast

from envcite_jobkit import EndpointError, ItemError, raise_for_status

USER_AGENT = "envcite-ontology (+https://gitlab.com/thuaisse-lab/envcite)"


class Http:
    """循序、帶禮貌間隔與身分的 HTTP。失敗依 jobkit 分類：端點問題可重試，其餘隔離或致命。"""

    def __init__(self, pause: float, *, legacy_tls: bool = False) -> None:
        """`legacy_tls`：站台不支援安全重新協商（RFC 5746）時才開；憑證照常驗證。"""
        self._pause = pause
        self._last = 0.0
        # 這幾站的憑證都缺少 Subject Key Identifier（同全國法規資料庫），只清除這一個旗標
        self._tls = ssl.create_default_context()
        self._tls.verify_flags &= ~ssl.VERIFY_X509_STRICT
        if legacy_tls:
            self._tls.options |= ssl.OP_LEGACY_SERVER_CONNECT

    def text(self, url: str) -> str:
        with self._open(url) as r:
            return cast(bytes, r.read()).decode("utf-8-sig")

    def form(self, url: str, fields: dict[str, str]) -> str:
        """POST 表單（`application/x-www-form-urlencoded`，UTF-8）。"""
        with self._open(url, urllib.parse.urlencode(fields).encode()) as r:
            return cast(bytes, r.read()).decode("utf-8-sig")

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        h = hashlib.sha256()
        size = 0
        with self._open(url) as r, dest.open("wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
                size += len(chunk)
        if size == 0:
            raise ItemError(f"{url}: empty response")
        return h.digest(), size

    def _open(self, url: str, data: bytes | None = None) -> Any:
        wait = self._last + self._pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(request, context=self._tls, timeout=60)
        except urllib.error.HTTPError as e:
            raise_for_status(e.code, f"{url}: {e}")
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, HTTPException) as e:
            raise EndpointError(f"{url}: {e}") from e
