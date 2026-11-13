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
from envcite_ontology.acquire.markup import visible_text
from envcite_ontology.ingest import numerals

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
_DISCRETION = re.compile(r"裁罰|裁量基準|罰鍰額度|缺失記點")
_INTERPRETATION = re.compile(r"解釋|釋示|疑義")
#: 依法定授權訂定之法規命令（裁罰準則、辦法、標準、細則），屬行政程序法第 150 條法規命令，
#: 不得誤歸為第 159 條第 2 項第 2 款之行政規則「裁量基準」。
_DELEGATED_REGULATION = re.compile(r"(?:準則|辦法|標準|細則)」?$")

type RuleKind = Literal["interpretation", "discretion_standard"]
type TitleClassification = Literal["interpretation", "discretion_standard", "delegated_regulation"]

#: 主旨裡的動作與其後的規則名稱：`修正「甲」、「乙」第三點，名稱並修正為「丙」`
_ACTION = re.compile(
    r"(?P<verb>名稱並修正為|名稱修正為|訂定|修正|廢止|停止適用)"
    r"(?P<names>(?:「[^」]+」(?:第[^，。、；「」]*?[點條項款])?(?:部分規定)?[、及與暨]?)+)"
)
_NAME = re.compile(r"「([^」]+)」((?:第[^，。、；「」]*?[點條項款])|部分規定)?")
#: 停止的生效日；寫「即日」或沒寫就是發文日
_EFFECTIVE = re.compile(r"自([^，。；]{4,24}?日)(?:起)?(?:生效|停止適用|廢止)")


class DelegatedRegulationTitle(ValueError):
    """標題為依法授權訂定之法規命令（如裁罰準則），屬第 150 條法規命令，非行政規則。"""


@dataclass(frozen=True, slots=True)
class Actions:
    """主旨所載對規則的動作，依名稱。名稱是規則在公報上的稱呼，不是識別碼。"""

    #: 本件訂定、修正（含部分修正）或改名所及的規則名稱
    sets: tuple[str, ...]
    #: 本件的改名（舊名，新名）：「修正「甲」，名稱並修正為「乙」」
    renames: tuple[tuple[str, str], ...]
    #: 本件整部廢止或停止適用的規則名稱（只廢止其中幾點的不算）
    repeals: tuple[str, ...]
    #: 主旨寫明的停止生效日；沒寫或「即日」為 None
    effective_on: date | None


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
        """這則行政規則在公報資訊網的詳細頁網址。"""
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


def classify_title(title: str) -> TitleClassification:
    """依標題區分法規命令（如裁罰準則）、行政規則裁量基準／記點原則，或解釋性規定。

    依法定授權訂定之「準則／辦法／標準／細則」屬行政程序法第 150 條法規命令，應入語料庫，
    不得誤歸為第 159 條第 2 項第 2 款之行政規則。
    """
    stripped = title.strip()
    if _DELEGATED_REGULATION.search(stripped):
        return "delegated_regulation"
    discretion = _DISCRETION.search(stripped) is not None
    if discretion and _INTERPRETATION.search(stripped):
        raise AmbiguousKind(title)
    return "discretion_standard" if discretion else "interpretation"


def rule_kind(title: str) -> RuleKind:
    """依標題字樣判斷行政規則是裁量基準或解釋性規定。

    標題屬法規命令（如「...裁罰準則」）時拋 `DelegatedRegulationTitle`；
    同時有裁量基準與解釋字樣時拋 `AmbiguousKind`。
    """
    kind = classify_title(title)
    if kind == "delegated_regulation":
        raise DelegatedRegulationTitle(title)
    return kind


def actions(subject: str) -> Actions:
    """`廢止「甲」，並自即日生效` → 廢止甲；`修正「甲」第三點` → 甲的修正。"""
    text = "".join(subject.split())
    sets: dict[str, None] = {}
    repeals: dict[str, None] = {}
    renames: list[tuple[str, str]] = []
    last: str | None = None
    for m in _ACTION.finditer(text):
        verb = m.group("verb")
        for name, part in _NAME.findall(m.group("names")):
            if verb in ("廢止", "停止適用"):
                if not part:
                    repeals.setdefault(name)
                continue
            if verb.startswith("名稱") and last is not None:
                renames.append((last, name))
            sets.setdefault(name)
            last = name
    effective = _EFFECTIVE.search(text)
    on = None
    if effective and repeals:
        written = numerals.DATE_BARE.search(effective.group(1))
        on = numerals.roc_date(*written.groups()) if written else None
    return Actions(tuple(sets), tuple(renames), tuple(repeals), on)


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
        text = visible_text(inner)
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
