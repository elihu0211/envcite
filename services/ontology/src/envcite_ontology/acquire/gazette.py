"""行政院公報資訊網（gazette.nat.gov.tw）的開放資料：只取行政規則。

開放資料頁提供本月每日一包（`openData03.do`）與民國 94 年起的歷史月包（`openData04.do`，
每月數包），都是 ZIP，內含每個出刊日一個 XML 與數個 PDF。下載網址帶不能由日期推導的雜湊，
只能從清單頁取得；沒有 JSON 清單 API。網站沒有 robots.txt（該路徑回 404）。資料集登錄於
政府資料開放平臺（資料集 5959），以**政府資料開放授權條款－第 1 版**釋出，利用時須顯名。
本模組只解析，不寫資料庫。

XML 每則刊登一個 `<Record>`；行政規則是 `Doc_Style_LName` 為「行政規則」者（行政程序法第 159 條
第 2 項第 2 款：公報只刊登這一款，第 160 條第 2 項）。欄位對應：發文機關 `PubGovName`、發文字號
`GazetteId`、發文日期 `Date_Created`、主旨 `ThemeSubject`、全文 `HTMLContent`、卷期由 `GazetteHTML`
的路徑推出（`eg032182` 為第 32 卷第 182 期）。
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from envcite_ontology.acquire.engine import InvalidSnapshot, NotAnArchive

BASE = "https://gazette.nat.gov.tw/egFront/"
#: 本月每日一包
MONTH_LIST = f"{BASE}openData03.do"
#: 某一年（民國三位數）的歷史月包
YEAR_LIST = f"{BASE}openData04.do?action=historyYearDaily&year={{year:03d}}"
ATTRIBUTION = "行政院公報資訊網（行政院）；政府資料開放授權條款－第1版"

_PACK_LINK = re.compile(
    r'href="(fileView\.do\?[^"]*?zipfile=([0-9]{3}-[0-9]{2}(?:-[0-9]{2}|_[0-9]+)\.zip))"'
)
_ROC_DATE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISSUE = re.compile(r"/eg0*(\d+?)(\d{3})/")
_XML_DATE = re.compile(r"(\d{3})-(\d{2})-(\d{2})\.xml$")
_PARAGRAPH = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.S)
#: 裁量基準的標題字樣（02-05 §4.3）；解釋的字樣用來判斷兩邊都沾得上的
_DISCRETION = re.compile(r"裁罰|裁量基準|罰鍰額度")
_INTERPRETATION = re.compile(r"解釋|釋示|疑義")

RuleKind = Literal["interpretation", "discretion_standard"]


class AmbiguousKind(ValueError):
    """標題同時像裁量基準與解釋性規定：不猜，記匯入問題。"""


@dataclass(frozen=True, slots=True)
class Pack:
    """清單上的一個 ZIP：檔名（如 `115-10_01.zip`）與下載位址。"""

    name: str
    url: str

    def covers_from(self, since: date) -> bool:
        """這一包是否可能含 `since` 當日或之後的刊登。"""
        roc_year, month, rest = int(self.name[:3]), int(self.name[4:6]), self.name[6:]
        if rest.startswith("-"):
            return date(roc_year + 1911, month, int(rest[1:3])) >= since
        return (roc_year + 1911, month) >= (since.year, since.month)


@dataclass(frozen=True, slots=True)
class Rule:
    """一則刊登的行政規則。缺的欄位為 None，由呼叫端記成匯入問題。"""

    meta_id: str
    issuer: str
    document_no: str
    issued_on: date | None
    title: str
    subject: str
    body: str
    published_in: str | None

    @property
    def source_url(self) -> str:
        return f"{BASE}detail.do?metaid={self.meta_id}"


def packs(listing: str) -> list[Pack]:
    """清單頁裡每個 ZIP，依出現順序、去重。"""
    found: dict[str, Pack] = {}
    for m in _PACK_LINK.finditer(listing):
        found.setdefault(m.group(2), Pack(m.group(2), BASE + html.unescape(m.group(1))))
    return list(found.values())


def roc_date(text: str) -> date | None:
    """`中華民國115年10月1日` → 西元日期；讀不出為 None。"""
    m = _ROC_DATE.search(text)
    if m is None:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def rule_kind(title: str) -> RuleKind:
    discretion = _DISCRETION.search(title) is not None
    if discretion and _INTERPRETATION.search(title):
        raise AmbiguousKind(title)
    return "discretion_standard" if discretion else "interpretation"


def published_in(gazette_html: str) -> str | None:
    """`…/eguploadpub/eg032182/…` → `行政院公報第32卷第182期`。"""
    m = _ISSUE.search(gazette_html)
    if m is None:
        return None
    return f"行政院公報第{int(m.group(1))}卷第{int(m.group(2))}期"


def body_text(content: str) -> str:
    """全文的段落：去掉發文機關、字號區塊、署名與附件連結；段落以換行相接。"""
    paragraphs = []
    for attrs, inner in _PARAGRAPH.findall(content):
        if "attachment" in attrs or 'class="title"' in inner:
            continue
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def read(path: Path) -> tuple[date, list[Rule]]:
    """一包的資料日期（最後一個出刊日）與其中的行政規則。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if _XML_DATE.search(n))
            if not names:
                raise InvalidSnapshot(f"{path.name}: no gazette XML")
            rules = [r for n in names for r in _rules(z.read(n))]
    except zipfile.BadZipFile as e:
        raise NotAnArchive(str(e)) from e
    except ET.ParseError as e:
        raise InvalidSnapshot(f"{path.name}: {e}") from e
    m = _XML_DATE.search(names[-1])
    assert m is not None
    roc_year, month, day = (int(g) for g in m.groups())
    return date(roc_year + 1911, month, day), rules


def _rules(data: bytes) -> Sequence[Rule]:
    rules = []
    for rec in ET.fromstring(data).iter("Record"):
        if _field(rec, "Doc_Style_LName") != "行政規則":
            continue
        rules.append(
            Rule(
                meta_id=_field(rec, "MetaId"),
                issuer=_field(rec, "PubGovName"),
                document_no=_field(rec, "GazetteId"),
                issued_on=roc_date(_field(rec, "Date_Created")),
                title=_field(rec, "Title"),
                subject=_field(rec, "ThemeSubject"),
                body=body_text(_field(rec, "HTMLContent")) or _field(rec, "Explain"),
                published_in=published_in(_field(rec, "GazetteHTML")),
            )
        )
    return rules


def _field(rec: ET.Element, tag: str) -> str:
    return (rec.findtext(tag) or "").strip()
