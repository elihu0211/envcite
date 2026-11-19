"""附表：法規底下的附表單元（`annex_<號>`）。

排放標準的限值寫在附表，條文只說「適用附表三」。附表是法規的一個單元，和條一樣是根：路徑
`annex_<號>`（附表十二之一是 `annex_12-1`），沒有項款目。全國法規資料庫的 API 只給附件的檔名與網址，
網站的 `robots.txt` 禁止抓檔；附表的檔案取自環境部主管法規查詢系統（`moenv_law`，CC BY 4.0，須註明
出處），所以這裡的來源只有環境部主管的法規。

附表文字是 PDF 的文字層，**照抽出的樣子存**，不改字、不重組表格：
表格的語意（業別、條件、限值）是標註管線的事。附表沒有文字層（掃描檔）或不是 PDF，這一版不收。

寫入與條文單元分開：全國法規資料庫的快照不含附表，所以條文的匯入（`write.apply`）不碰附表單元，
這裡也只碰附表單元。附表的效力區間與版本跟隨該法規現行版本的第一個條文單元。
"""

import html
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from psycopg.types.range import Range
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from canon.db import Conn, fetch, fetch_one
from canon.law.write import content_sha
from canon.numerals import CN_NUMERALS, parse_int

#: 環境部系統附件的下載網址（相對於 `moenv_law.BASE`）；附件連結只認這個形狀。
_LINK = re.compile(
    r'<a[^>]*href="(Download\.ashx\?FileID=(\d+)&(?:amp;)?id=[^"&]+&(?:amp;)?type=LAW)"[^>]*>(.*?)</a>',
    re.S,
)
_NAME = re.compile(
    rf"^附表\s*(?P<no>[{CN_NUMERALS}]+|\d+)(?:\s*之\s*(?P<sub>[{CN_NUMERALS}]+|\d+))?"
)
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
#: 字數低於這個的 PDF 視為沒有文字層。
MIN_CHARS = 20
MAX_PAGES = 200


@dataclass(frozen=True)
class Link:
    """環境部頁面上的一個附表連結。"""

    path: str
    name: str
    file_id: str
    #: 相對於環境部法規系統的下載網址。
    url: str


@dataclass(frozen=True)
class Annex:
    """一個附表：單元路徑與抽出的全文。"""

    path: str
    text: str


def annex_path(name: str) -> str | None:
    """附表檔名 → 單元路徑：「附表三 …」→ `annex_3`、「附表十二之一」→ `annex_12-1`。

    不是附表為 `None`。
    """
    found = _NAME.match(name.strip())
    if found is None:
        return None
    try:
        number = _number(found.group("no"))
        sub = found.group("sub")
        return f"annex_{number}" if sub is None else f"annex_{number}-{_number(sub)}"
    except ValueError:
        return None


def _number(text: str) -> int:
    return int(text) if text.isdigit() else parse_int(text)


def links(page: str) -> list[Link]:
    """法規頁上的附表連結（檔名以「附表」開頭、是 PDF）；同一個號數只收第一個。"""
    found: dict[str, Link] = {}
    for match in _LINK.finditer(page):
        name = _SPACE.sub(" ", html.unescape(_TAGS.sub("", match.group(3)))).strip()
        path = annex_path(name)
        if path is None or not name.lower().endswith(".pdf"):
            continue
        found.setdefault(path, Link(path, name, match.group(2), html.unescape(match.group(1))))
    return list(found.values())


def extract_text(data: bytes) -> str | None:
    """PDF 的文字層（每頁依序，頁間空一行）；不是可讀的 PDF 或字數不足（掃描檔）為 `None`。"""
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PAGES:
            return None
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except PyPdfError, ValueError, OSError, KeyError, TypeError:
        return None
    text = "\n\n".join(p for p in pages if p)
    return text if len(text) >= MIN_CHARS else None


class _Reference(NamedTuple):
    expression: int
    force: Range[date] | None
    rule: str


class _Current(NamedTuple):
    id: int
    provision: int
    path: str
    expression: int
    sha: bytes
    force: Range[date] | None


class _Id(NamedTuple):
    id: int


def write(conn: Conn, pcode: str, annexes: list[Annex], *, prune: bool) -> str:
    """把附表寫進一部法規；回傳 `absent`、`unchanged` 或 `written`。

    `absent`：語料沒有這部法規，或它的現行版本沒有條文。

    每個附表的現行版本與文字相同就不動；文字不同或法規有了新版本，就結束舊版本，新增一個附在法規
    最新版本上的新版本（舊版本的效力止於新版本的起點）。`prune` 為真時，這次沒有的附表也結束
    （只在列舉完整時才傳真，部分失敗不能讓附表消失）。不 commit，由呼叫端的交易包住。
    """
    resource = fetch_one(
        conn,
        "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
        (pcode,),
        _Id,
    )
    if resource is None:
        return "absent"
    reference = fetch_one(
        conn,
        "SELECT v.expression_id, v.force_period, v.commencement_rule::text"
        "  FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
        " WHERE v.resource_id = %s AND v.superseded_at IS NULL AND p.provision_type = 'article'"
        "   AND e.language = 'zho'"
        " ORDER BY e.version_date DESC, p.sort_key LIMIT 1",
        (resource.id,),
        _Reference,
    )
    if reference is None:
        return "absent"

    current = {
        c.path: c
        for c in fetch(
            conn,
            "SELECT v.id, v.provision_id, p.path, v.expression_id, v.content_sha256, v.force_period"
            "  FROM corpus.provision_version AS v"
            "  JOIN corpus.provision AS p ON p.id = v.provision_id"
            " WHERE v.resource_id = %s AND v.superseded_at IS NULL AND p.provision_type = 'annex'"
            # 舊版本結束後留下的、有終點的列是歷史，不是現行；
            # 現行的是沒有終點（或起點未知）的那一列。
            "   AND (v.force_period IS NULL OR upper_inf(v.force_period))",
            (resource.id,),
            _Current,
        )
    }
    wrote = False
    for annex in annexes:
        before = current.get(annex.path)
        if (
            before is not None
            and before.expression == reference.expression
            and before.sha == content_sha(annex.text)
        ):
            continue
        _put(conn, resource.id, reference, annex, before)
        wrote = True
    if prune:
        for path, before in current.items():
            if path not in {a.path for a in annexes}:
                conn.execute(
                    "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = %s",
                    (before.id,),
                )
                wrote = True
    return "written" if wrote else "unchanged"


def _put(
    conn: Conn, resource: int, reference: _Reference, annex: Annex, before: _Current | None
) -> None:
    conn.execute(
        "INSERT INTO corpus.provision (resource_id, parent_id, provision_type, path)"
        " VALUES (%s, NULL, 'annex', %s) ON CONFLICT (resource_id, path) DO NOTHING",
        (resource, annex.path),
    )
    if before is not None:
        conn.execute(
            "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = %s", (before.id,)
        )
        new_start = reference.force.lower if reference.force is not None else None
        old = before.force
        if (
            before.expression != reference.expression
            and old is not None
            and old.lower is not None
            and new_start is not None
            and old.lower < new_start
        ):
            # 舊版本的效力止於新版本的起點。
            conn.execute(
                "INSERT INTO corpus.provision_version"
                " (provision_id, expression_id, resource_id, content, force_period,"
                "  commencement_rule)"
                " SELECT provision_id, expression_id, resource_id, content, %s, commencement_rule"
                "   FROM corpus.provision_version WHERE id = %s",
                (Range(old.lower, new_start, "[)"), before.id),
            )
    conn.execute(
        "INSERT INTO corpus.provision_version"
        " (provision_id, expression_id, resource_id, content, force_period, commencement_rule)"
        " VALUES ((SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
        "         %s, %s, %s, %s, %s::corpus.commencement_rule)",
        (
            resource,
            annex.path,
            reference.expression,
            resource,
            annex.text,
            reference.force,
            reference.rule,
        ),
    )
