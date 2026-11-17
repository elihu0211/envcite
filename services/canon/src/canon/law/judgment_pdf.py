"""裁判書全文是 PDF 時：下載、抽文字層，與 HTML 全文走同一條入庫路徑。

只信司法院的網址（https 且主機在 `judicial.gov.tw`）：網址來自 API 回應，不當成任意下載來源。
只抽文字層，不做 OCR；掃描檔沒有文字層，回 `None`，由呼叫端記 `file_only`。
下載走 `acquire/web.py` 的 `Http`（循序、禮貌間隔、自報身分），大小與頁數有上限。
"""

import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from canon.acquire.web import Http
from canon.law.judicial_api import EndpointError
from envcite_jobkit import EndpointError as TransportFailure
from envcite_jobkit import ItemError

#: 超過就不抽（一般裁判書遠小於此）。
MAX_BYTES = 20 << 20
MAX_PAGES = 400
#: 抽出的字少於此視為沒有文字層（掃描檔）。
MIN_CHARS = 200
_HOST_SUFFIX = "judicial.gov.tw"
#: https 加主機，沒有帳號密碼（`@`），可有埠號；主機以外的部分不看。
_URL = re.compile(r"https://([A-Za-z0-9.-]+)(?::\d+)?(?:[/?#]|$)")

#: 取一個網址的全文：有文字回字串，沒有文字層回 `None`；網址不可信或檔案不合時丟 `PdfRejected`。
type FileText = Callable[[str], str | None]


class PdfRejected(ValueError):
    """網址不可信、檔案過大、或不是可讀的 PDF：重試不會改變結果。"""


def trusted(url: str) -> bool:
    """https 且主機是司法院網域。"""
    found = _URL.match(url)
    host = found.group(1).lower() if found else ""
    return host == _HOST_SUFFIX or host.endswith("." + _HOST_SUFFIX)


def text_of(path: Path) -> str | None:
    """PDF 的文字層，空白正規化；字數不足回 `None`。檔案不是可讀的 PDF 丟 `PdfRejected`。"""
    try:
        reader = PdfReader(path)
        if len(reader.pages) > MAX_PAGES:
            raise PdfRejected("too_many_pages")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError, ValueError, OSError, KeyError, TypeError:
        raise PdfRejected("unreadable") from None
    text = re.sub(r"[ \t　]+", " ", "\n".join(pages))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) >= MIN_CHARS else None


def fetcher(http: Http) -> FileText:
    """以 `Http` 下載再抽字的 `FileText`。連線與伺服器錯誤轉成 `EndpointError`，留待下一夜重試。"""

    def fetch(url: str) -> str | None:
        if not trusted(url):
            raise PdfRejected("untrusted_url")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "judgment.pdf"
            try:
                _, size = http.file(url, dest)
            except TransportFailure:
                raise EndpointError("pdf") from None
            except ItemError:
                raise PdfRejected("download_failed") from None
            if size > MAX_BYTES:
                raise PdfRejected("too_large")
            return text_of(dest)

    return fetch
