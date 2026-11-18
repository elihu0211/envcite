"""裁判書全文是 PDF 時：下載、抽文字層，與 HTML 全文走同一條入庫路徑。

只信司法院的網址（https 且主機在 `judicial.gov.tw`）：網址來自 API 回應，不當成任意下載來源。
逐頁判斷：文字層夠的頁用文字層；文字層近乎空白且頁面有圖片的是掃描頁，設定了 OCR 模型時轉成圖片
交給 OCR，掃描頁數有上限。沒有 OCR 或讀完仍沒有字，回 `None`，由呼叫端記 `file_only`。
下載走 `acquire/web.py` 的 `Http`（循序、禮貌間隔、自報身分），大小與頁數有上限。
"""

import io
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from canon.acquire.web import Http
from canon.law.judicial_api import EndpointError
from canon.ocr import OcrError, OcrIncomplete, Reads
from envcite_jobkit import EndpointError as TransportFailure
from envcite_jobkit import ItemError

#: 超過就不抽（一般裁判書遠小於此）。
MAX_BYTES = 20 << 20
MAX_PAGES = 400
#: OCR 逐頁呼叫模型，掃描頁數上限比文字層的頁數上限小得多；超過的不做。
OCR_MAX_PAGES = 60
#: 一頁的文字層少於此字數且頁面有圖片，視為掃描頁。裁判書最短的一頁（末頁的「以上正本」等）
#: 也有十餘字以上；掃描檔的文字層通常是空的。沒有圖片的近空白頁不是掃描頁，送去讀只會讓模型編字。
PAGE_MIN_CHARS = 20
#: 轉圖片的縮放（72 dpi 的倍數）；2 倍約 144 dpi，足夠辨識一般字級。
OCR_SCALE = 2.0
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
    return read_text(path, None)


def read_text(path: Path, ocr: Reads | None) -> str | None:
    """PDF 的全文：每頁文字層夠就用，掃描頁且有 `ocr` 才讀圖；字數不足回 `None`。

    不能只看全文字數：文字頁夾著掃描頁的 PDF 光靠文字層就過關，掃描頁的內容會靜默遺失。
    檔案不是可讀的 PDF 或掃描頁超限丟 `PdfRejected`，OCR 失敗丟 `OcrError`。
    """
    try:
        reader = PdfReader(path)
        if len(reader.pages) > MAX_PAGES:
            raise PdfRejected("too_many_pages")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError, ValueError, OSError, KeyError, TypeError:
        raise PdfRejected("unreadable") from None
    if ocr is not None:
        _read_scanned(path, pages, ocr)
    return _enough(pages)


def _enough(pages: list[str]) -> str | None:
    """各頁文字併成全文、空白正規化；字數不足回 `None`。"""
    text = re.sub(r"[ \t\u3000]+", " ", "\n".join(pages))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) >= MIN_CHARS else None


_LINE_NO = re.compile(r"^(\d{2})(?:\s+|(?=\D)|$)")


def strip_line_numbers(page: str) -> str:
    """裁判書每頁左側印有逐行編號（01、02…）；OCR 會把它讀成正文的開頭。

    行首的兩位數依序遞增（01、02、03…）且至少連續 5 行，才視為行號去掉；正文裡偶然以數字開頭的
    行（日期、金額）不會連成這樣的序列。
    """
    lines = page.split("\n")
    found = [(i, int(m.group(1))) for i, line in enumerate(lines) if (m := _LINE_NO.match(line))]
    run: list[int] = []
    expected = 1
    for index, number in found:
        if number == expected:
            run.append(index)
            expected += 1
    if len(run) < 5:
        return page
    drop = set(run)
    return "\n".join(
        _LINE_NO.sub("", line, count=1) if i in drop else line for i, line in enumerate(lines)
    )


def _read_scanned(path: Path, pages: list[str], ocr: Reads) -> None:
    """掃描頁（文字層近乎空白、頁面有圖片）原地換成 OCR 讀出的字，其餘頁不動。"""
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        raise PdfRejected("unreadable") from None
    try:
        if len(pdf) != len(pages):
            raise PdfRejected("unreadable")
        scanned = [
            index
            for index, layer in enumerate(pages)
            if len(layer.strip()) < PAGE_MIN_CHARS and _has_image(pdf[index])
        ]
        if len(scanned) > OCR_MAX_PAGES:
            raise PdfRejected("too_many_pages")
        for index in scanned:
            pages[index] = strip_line_numbers(ocr.read(_jpeg(pdf[index])))
    finally:
        pdf.close()


def _has_image(page: pdfium.PdfPage) -> bool:
    return any(True for _ in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)))


def _jpeg(page: pdfium.PdfPage) -> bytes:
    image = page.render(scale=OCR_SCALE).to_pil().convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def fetcher(http: Http, ocr: Reads | None = None) -> FileText:
    """以 `Http` 下載再抽字的 `FileText`。連線、伺服器與 OCR 的錯誤轉成 `EndpointError`。"""

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
            try:
                return read_text(dest, ocr)
            except OcrIncomplete:
                raise PdfRejected("ocr_incomplete") from None
            except OcrError:
                raise EndpointError("ocr") from None

    return fetch
