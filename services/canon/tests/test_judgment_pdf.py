"""PDF 全文只信司法院網址，只抽文字層；沒有文字層或不是 PDF 都不當成全文。"""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from canon.law.judgment_pdf import MIN_CHARS, PdfRejected, text_of, trusted


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
