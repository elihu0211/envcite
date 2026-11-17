"""以網址（與表單的檢索詞）對到頁面的替身，取代 `acquire.web.Http`；沒有的頁是 404。"""

from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest

from canon import net
from canon.acquire.web import Http
from envcite_jobkit import ItemError


class FakeWeb:
    def __init__(self, pages: dict[str, str], forms: dict[tuple[str, str], str] | None = None):
        self.pages = pages
        self.forms = forms or {}
        self.requested: list[str] = []

    def text(self, url: str) -> str:
        self.requested.append(url)
        if url not in self.pages:
            raise ItemError(f"404 {url}")
        return self.pages[url]

    def form(self, url: str, fields: dict[str, str]) -> str:
        key = (url, fields.get("_1_6_T", ""))
        self.requested.append(f"POST {url} {key[1]}")
        if key not in self.forms:
            raise ItemError(f"404 POST {url}")
        return self.forms[key]


class Served:
    """以網址對到檔案的傳輸替身：換掉 `Http` 的網路，下載走完整的 httpx 路徑。

    `failures` 依序決定前幾次請求回的狀態碼，之後照常回檔案；沒有的網址是 404。檔案在請求時
    才讀，測試可以先建物件、後寫檔。
    """

    def __init__(self, files: dict[str, Path], failures: Sequence[int] = ()) -> None:
        self.files = files
        self.failures = list(failures)
        #: 每次請求的網址，依次序。
        self.requested: list[str] = []

    def http(self) -> Http:
        """換上這個傳輸、不設禮貌間隔的客戶端。"""
        return Http(0.0, transport=httpx.MockTransport(self._respond))

    def _respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)
        if self.failures:
            return httpx.Response(self.failures.pop(0))
        path = self.files.get(url)
        if path is None:
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())


def route_post_json(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """讓 `canon.net.post_json` 的請求由 `handler` 回應，不碰網路；測試結束時還原。"""
    monkeypatch.setattr(net, "_pool", httpx.Client(transport=httpx.MockTransport(handler)))
