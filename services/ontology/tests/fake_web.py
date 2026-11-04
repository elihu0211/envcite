"""以網址（與表單的檢索詞）對到頁面的替身，取代 `acquire.web.Http`；沒有的頁是 404。"""

from __future__ import annotations

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
