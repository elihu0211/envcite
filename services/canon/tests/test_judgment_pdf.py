"""PDF 全文只信司法院網址，只抽文字層；沒有文字層或不是 PDF 都不當成全文。"""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from canon.law.judgment_pdf import (
    MIN_CHARS,
    OCR_MAX_PAGES,
    PdfRejected,
    ocr_text,
    strip_line_numbers,
    text_of,
    trusted,
)


def _pdf_with_text(text: str) -> bytes:
    """最小的單頁 PDF，內容串流用 Helvetica 畫一行字。"""
    stream = f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://data.judicial.gov.tw/a.pdf", True),
        ("https://judgment.judicial.gov.tw/x", True),
        ("http://data.judicial.gov.tw/a.pdf", False),
        ("https://evil.example/a.pdf", False),
        ("https://judicial.gov.tw.evil.example/a.pdf", False),
        ("https://notjudicial.gov.tw/a.pdf", False),
    ],
)
def test_only_https_on_the_judicial_domain_is_trusted(url: str, expected: bool) -> None:
    assert trusted(url) is expected


def test_the_text_layer_is_extracted(tmp_path: Path) -> None:
    sentence = "Noise Control Act article two applies to this administrative case. " * 5
    path = tmp_path / "a.pdf"
    path.write_bytes(_pdf_with_text(sentence.strip()))

    text = text_of(path)

    assert text is not None and len(text) >= MIN_CHARS
    assert "Noise Control Act article two" in text


def test_a_page_without_text_is_not_a_body(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    path = tmp_path / "scan.pdf"
    with path.open("wb") as f:
        writer.write(f)

    assert text_of(path) is None


def test_a_file_that_is_not_a_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.pdf"
    path.write_bytes(b"<html>not a pdf</html>")

    with pytest.raises(PdfRejected):
        text_of(path)


class _Ocr:
    model = "vl"

    def __init__(self, text: str) -> None:
        self.text = text
        self.images: list[bytes] = []

    def read(self, image: bytes, *, timeout: float | None = None) -> str:
        self.images.append(image)
        return self.text


def _blank_pdf(path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(612, 792)
    with path.open("wb") as f:
        writer.write(f)
    return path


def test_a_scanned_pdf_is_read_page_by_page_by_the_ocr(tmp_path: Path) -> None:
    ocr = _Ocr("本件適用噪音管制法第二條，應處罰鍰。" * 8)
    path = _blank_pdf(tmp_path / "scan.pdf", 2)

    text = ocr_text(path, ocr)

    assert text is not None and text.count("本件適用噪音管制法第二條") == 16
    assert len(ocr.images) == 2 and all(img.startswith(b"\xff\xd8") for img in ocr.images)


def test_ocr_that_finds_no_words_is_still_no_body(tmp_path: Path) -> None:
    assert ocr_text(_blank_pdf(tmp_path / "scan.pdf", 1), _Ocr("")) is None


def test_too_many_pages_are_not_sent_to_the_ocr(tmp_path: Path) -> None:
    ocr = _Ocr("x")
    path = _blank_pdf(tmp_path / "long.pdf", OCR_MAX_PAGES + 1)

    with pytest.raises(PdfRejected, match="too_many_pages"):
        ocr_text(path, ocr)

    assert ocr.images == []


def test_the_printed_line_numbers_are_dropped_from_an_ocr_page() -> None:
    page = "\n".join(f"{n:02d} 第{n}行的正文" for n in range(1, 9))

    text = strip_line_numbers(page)

    assert text.split("\n")[0] == "第1行的正文" and "01" not in text and len(text.split("\n")) == 8


def test_numbers_that_are_not_a_running_sequence_are_kept() -> None:
    page = "12 月 3 日\n50 萬元\n原告主張如上\n08 年度"

    assert strip_line_numbers(page) == page


def test_a_line_that_is_only_its_number_is_dropped_too() -> None:
    page = "01 標題\n02\n03 原告\n04 被告\n05 主文\n06 駁回"

    assert strip_line_numbers(page).split("\n") == ["標題", "", "原告", "被告", "主文", "駁回"]
