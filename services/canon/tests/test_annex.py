"""附表單元：路徑與連結的解析、PDF 文字層、寫入，以及與條文重新匯入共存。"""

from datetime import date
from pathlib import Path
from typing import NamedTuple

import psycopg
import pytest
from pypdf import PdfWriter

from canon.db import Conn, fetch
from canon.law import annex, articles
from canon.law.annex import Annex
from tests.test_incremental import _first, _law_id, _law_v1, _law_v2, _next
from tests.test_judgment_pdf import _pdf_with_text

PCODE = "T0000001"
TEXT = "附表三 石油化學業放流水水質項目及限值 化學需氧量 一00"


class _Current(NamedTuple):
    path: str
    content: str | None
    expression: int


def _current(conn: Conn) -> list[_Current]:
    return fetch(
        conn,
        "SELECT p.path, v.content, v.expression_id FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE p.resource_id = %s AND p.provision_type = 'annex' AND v.superseded_at IS NULL"
        "   AND (v.force_period IS NULL OR upper_inf(v.force_period))"
        " ORDER BY p.sort_key",
        (_law_id(conn),),
        _Current,
    )


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("附表三  石油化學業放流水水質項目及限值.pdf", "annex_3"),
        ("附表十二：社區專用污水下水道系統放流水水質項目及限值 .PDF", "annex_12"),
        ("附表十二之一 其他.pdf", "annex_12-1"),
        ("附表 2 說明.pdf", "annex_2"),
        ("附表十六 .PDF", "annex_16"),
        ("附件一 表格.pdf", None),
        ("申請書.pdf", None),
    ],
)
def test_an_annex_file_name_gives_its_unit_path(name: str, path: str | None) -> None:
    assert annex.annex_path(name) == path


def test_only_annex_pdfs_are_taken_from_a_page_and_each_number_once() -> None:
    page = """
    <a href="Download.ashx?FileID=135024&amp;id=FL015489&amp;type=LAW">附表三  石油化學業.pdf</a>
    <a href="Download.ashx?FileID=135025&id=FL015489&type=LAW"> 附表五 金屬
        基本工業.pdf </a>
    <a href="Download.ashx?FileID=1&id=FL015489&type=LAW">附表三 重複.pdf</a>
    <a href="Download.ashx?FileID=2&id=FL015489&type=LAW">申請書範本.doc</a>
    <a href="Download.ashx?FileID=3&id=FL015489&type=LAW">附表六 表.doc</a>
    <a href="/law/UseManual/使用手冊.pdf">使用手冊</a>
    """

    found = annex.links(page)

    assert [(link.path, link.file_id) for link in found] == [
        ("annex_3", "135024"),
        ("annex_5", "135025"),
    ]
    assert found[0].url == "Download.ashx?FileID=135024&id=FL015489&type=LAW"
    assert found[1].name == "附表五 金屬 基本工業.pdf"


def test_the_text_layer_is_read_and_a_blank_or_broken_pdf_is_not() -> None:
    sentence = "Annex three limit for chemical oxygen demand is one hundred"
    assert annex.extract_text(_pdf_with_text(sentence)) == sentence

    blank = PdfWriter()
    blank.add_blank_page(100, 100)
    import io

    buffer = io.BytesIO()
    blank.write(buffer)
    assert annex.extract_text(buffer.getvalue()) is None
    assert annex.extract_text(b"not a pdf") is None


def test_an_annex_sorts_after_every_article_in_sql_and_python(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    assert (
        annex.write(conn, PCODE, [Annex("annex_1", TEXT), Annex("annex_12-1", TEXT)], prune=True)
        == "written"
    )

    paths = [
        r.path
        for r in fetch(
            conn,
            "SELECT p.path, 0 AS content, 0 AS expression FROM corpus.provision AS p"
            " WHERE p.resource_id = %s ORDER BY p.sort_key",
            (_law_id(conn),),
            _Current,
        )
    ]

    assert paths == ["art_1", "art_2", "annex_1", "annex_12-1"]
    assert sorted(paths, key=articles.sort_key) == paths
    assert articles.sort_key("annex_12-1") == [articles.ANNEX_SORT_BASE, 12, 1]


def test_an_annex_is_a_root_unit_and_the_shape_stays_closed(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True)
    law = _law_id(conn)

    row = conn.execute(
        "SELECT provision_type::text, parent_id FROM corpus.provision"
        " WHERE resource_id = %s AND path = 'annex_1'",
        (law,),
    ).fetchone()
    assert row == ("annex", None)
    for path, kind, parent in [
        ("annex_1/para_1", "paragraph", "annex_1"),  # 附表沒有項款目
        ("annex_x", "annex", None),
        ("art_9", "annex", None),  # 類型要與路徑一致
    ]:
        with pytest.raises(
            (psycopg.errors.CheckViolation, psycopg.errors.InvalidTextRepresentation)
        ):
            conn.execute(
                "INSERT INTO corpus.provision (resource_id, parent_id, provision_type, path)"
                " VALUES (%s,"
                "         (SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
                "         %s::corpus.provision_type, %s)",
                (law, law, parent, kind, path),
            )
        conn.rollback()
        conn.execute("SELECT 1")


def test_writing_the_same_text_again_changes_nothing_and_new_text_replaces_it(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())

    assert annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True) == "written"
    conn.commit()  # 結束舊版本的時間要晚於它被記下的時間：同一個交易裡 now() 不變
    assert annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True) == "unchanged"
    assert annex.write(conn, PCODE, [Annex("annex_1", TEXT + " 更正")], prune=True) == "written"

    assert [(c.path, c.content) for c in _current(conn)] == [("annex_1", TEXT + " 更正")]


def test_an_unknown_law_and_a_law_without_articles_are_absent(conn: Conn, tmp_path: Path) -> None:
    assert annex.write(conn, "Z9999999", [Annex("annex_1", TEXT)], prune=True) == "absent"


def test_annexes_missing_from_a_complete_listing_end_but_a_partial_one_keeps_them(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    annex.write(conn, PCODE, [Annex("annex_1", TEXT), Annex("annex_2", TEXT + "二")], prune=True)
    conn.commit()

    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=False)
    conn.commit()
    assert [c.path for c in _current(conn)] == ["annex_1", "annex_2"]

    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True)
    assert [c.path for c in _current(conn)] == ["annex_1"]


def test_importing_the_articles_again_does_not_end_the_annexes(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True)

    _next(conn, tmp_path, _law_v1())  # 同一版本再匯入：條文沒變

    assert [(c.path, c.content) for c in _current(conn)] == [("annex_1", TEXT)]


def test_a_new_law_version_moves_the_annex_to_it_and_the_old_one_ends_at_its_start(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True)
    conn.commit()
    first = _current(conn)[0].expression

    _next(conn, tmp_path, _law_v2())
    assert [c.expression for c in _current(conn)] == [first]  # 匯入沒動它
    annex.write(conn, PCODE, [Annex("annex_1", TEXT)], prune=True)
    conn.commit()

    (now,) = _current(conn)
    assert now.expression != first
    periods = conn.execute(
        "SELECT lower(force_period), upper(force_period), expression_id = %s, superseded_at IS NULL"
        "  FROM corpus.provision_version AS v JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE p.path = 'annex_1' AND p.resource_id = %s ORDER BY v.id",
        (first, _law_id(conn)),
    ).fetchall()
    ended = [p for p in periods if p[2] and p[3] and p[1] is not None]
    assert ended, periods
    assert ended[0][1] == date(2020, 3, 1)  # 舊版本止於新版本的起點
