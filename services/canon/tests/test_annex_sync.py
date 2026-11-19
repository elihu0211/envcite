"""環境部命令的附表：一部法規從頁面連結、下載、抽字到寫成單元的流程（網路換成替身）。"""

import io
from datetime import date
from pathlib import Path

import httpx
from pypdf import PdfWriter

from canon.acquire.web import Http
from canon.db import Conn
from canon.law import annex_sync, moenv_law
from canon.law.history_sources import Target
from tests.test_annex import PCODE, _current
from tests.test_incremental import _first, _law_v1
from tests.test_judgment_pdf import _pdf_with_text

SITE = "FL000001"
PAGE = (
    f'<a href="Download.ashx?FileID=11&amp;id={SITE}&amp;type=LAW">附表一 限值.pdf</a>'
    f'<a href="Download.ashx?FileID=12&amp;id={SITE}&amp;type=LAW">附表二 掃描.pdf</a>'
)
SENTENCE = "Annex one limit for chemical oxygen demand is one hundred"
TARGET = Target(SITE, "噪音管制法", "命令")


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _web(requested: list[str]) -> Http:
    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url == moenv_law.CURRENT.format(site_id=SITE):
            return httpx.Response(200, content=PAGE.encode())
        if "FileID=11" in url:
            return httpx.Response(200, content=_pdf_with_text(SENTENCE))
        if "FileID=12" in url:
            return httpx.Response(200, content=_blank_pdf())
        return httpx.Response(404)

    return Http(0.0, transport=httpx.MockTransport(respond))


def test_a_law_page_gives_its_annex_units_and_a_scanned_one_is_left_out_without_pruning(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    requested: list[str] = []

    result = annex_sync._one(conn, _web(requested), TARGET, PCODE, dry_run=False)

    assert result == "written"
    assert [(c.path, c.content) for c in _current(conn)] == [("annex_1", SENTENCE)]
    assert requested[0] == moenv_law.CURRENT.format(site_id=SITE)
    assert f"{moenv_law.BASE}Download.ashx?FileID=11&id={SITE}&type=LAW" in requested


def test_a_dry_run_downloads_but_writes_nothing(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())

    annex_sync._one(conn, _web([]), TARGET, PCODE, dry_run=True)

    assert _current(conn) == []


def test_a_page_without_annexes_is_left_alone(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    web = Http(
        0.0, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html/>"))
    )

    assert annex_sync._one(conn, web, TARGET, PCODE, dry_run=False) == "no_annex"


def test_a_title_is_matched_only_when_exactly_one_law_has_it() -> None:
    named = {
        ("甲", "命令"): [("P1", date(2020, 1, 1))],
        ("乙", "命令"): [("P2", date(2020, 1, 1)), ("P3", date(2021, 1, 1))],
    }

    assert annex_sync._pcodes(named, Target("a", "甲", "命令")) == "P1"
    assert annex_sync._pcodes(named, Target("b", "乙", "命令")) is None
    assert annex_sync._pcodes(named, Target("c", "丙", "命令")) is None
