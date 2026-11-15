"""同步需要的網頁客戶端介面。"""

from pathlib import Path
from typing import Protocol


class Client(Protocol):
    """同步需要的網頁客戶端：抓文字頁與下載檔案（`acquire.web.Http`）。"""

    def text(self, url: str) -> str:
        """GET 一頁，回傳解碼後的文字。"""
        ...

    def file(self, url: str, dest: Path) -> tuple[bytes, int]:
        """下載到 `dest`；回傳（SHA-256，位元組數）。"""
        ...
