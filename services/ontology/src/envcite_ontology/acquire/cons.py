"""憲法法庭網站（cons.judicial.gov.tw）的解釋與判決頁。

來源只有 HTML：沒有 API、RSS 或下載檔，也沒有 robots.txt（該路徑回 404 頁）。網站的
「資料開放宣告」（docdata.aspx?fid=2122）以無償、非專屬、可再授權的方式授權重製、改作與公開傳輸，
**使用時須註明出處**，且不得惡意變更資訊。本模組只讀取頁面並解析成純資料，不寫資料庫。

頁面形狀（解析只依這些，其餘忽略）：每個欄位是一對 `<li class="title">欄位名</li>` 與
`<li class="text">…</li>`；解釋文與主文的段落在 `<pre>` 裡。
"""

from __future__ import annotations

import re
import ssl
from dataclasses import dataclass
from datetime import date
from typing import Literal

from lxml.html import HtmlElement, tostring

from envcite_ontology.acquire.markup import parse as parse_html
from envcite_ontology.acquire.markup import visible_text
from envcite_ontology.http import tls_context

BASE = "https://cons.judicial.gov.tw"

#: 解釋清單（分頁，每頁二十筆）與判決清單（單頁）。
INTERPRETATION_LIST = f"{BASE}/judcurrent.aspx?fid=2195&page={{page}}&tab=1"
JUDGMENT_LIST = f"{BASE}/judcurrentNew1.aspx?fid=38"

_DOC_HREF = re.compile(r"^/docdata\.aspx\?fid=(?:100|38)&id=\d+$")
_ROC_DATE = re.compile(r"(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_INTERPRETATION_NO = re.compile(r"釋字第\s*(\d+)\s*號")
_JUDGMENT_NO = re.compile(r"(\d{2,3})\s*年\s*憲判字第\s*(\d+)\s*號")
_PAGES = re.compile(r"page=(\d+)&(?:amp;)?tab=1")


class UnreadablePage(ValueError):
    """頁面抓到了，卻不是預期的解釋或判決頁。"""


@dataclass(frozen=True, slots=True)
class RulingPage:
    ruling_no: str
    kind: Literal["interpretation", "judgment"]
    decided_on: date
    source_url: str
    #: 解釋文（判決主文）全文，段落以換行相接。
    main_text: str


def tls() -> ssl.SSLContext:
    return tls_context()


def roc_date(text: str) -> date:
    """民國 `106年05月24日` → 西元日期。"""
    m = _ROC_DATE.search(text)
    if m is None:
        raise UnreadablePage(f"找不到日期：{text!r}")
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError as e:
        raise UnreadablePage(f"日期不合：{text!r}") from e


def document_links(listing: str, base: str = BASE) -> list[str]:
    """清單頁裡每則裁判頁的絕對位址，依出現順序、去重。"""
    found = (
        href
        for a in parse_html(listing).iter("a")
        if (href := a.get("href")) is not None and _DOC_HREF.match(href)
    )
    return [base + path for path in dict.fromkeys(found)]


def last_page(listing: str) -> int:
    """解釋清單的最後一頁頁碼。"""
    pages = [int(n) for n in _PAGES.findall(listing)]
    return max(pages, default=1)


def parse(page: str, url: str) -> RulingPage:
    """解析一則解釋或判決頁；格式不合拋 `UnreadablePage`。"""
    fields = _fields(page)
    if "解釋字號" in fields:
        number = _INTERPRETATION_NO.search(_text(fields["解釋字號"]))
        if number is None:
            raise UnreadablePage(f"{url}: 解釋字號不合")
        stamp = fields.get("解釋公布院令")
        if stamp is None:
            raise UnreadablePage(f"{url}: 沒有解釋公布院令")
        return RulingPage(
            ruling_no=f"釋字第{number.group(1)}號",
            kind="interpretation",
            decided_on=roc_date(_text(stamp)),
            source_url=url,
            main_text=_paragraphs(fields.get("解釋文")),
        )
    if "判決字號" in fields:
        number = _JUDGMENT_NO.search(_text(fields["判決字號"]))
        if number is None:
            raise UnreadablePage(f"{url}: 判決字號不合")
        stamp = fields.get("判決日期")
        if stamp is None:
            raise UnreadablePage(f"{url}: 沒有判決日期")
        return RulingPage(
            ruling_no=f"{int(number.group(1))}年憲判字第{int(number.group(2))}號",
            kind="judgment",
            decided_on=roc_date(_text(stamp)),
            source_url=url,
            main_text=_paragraphs(fields.get("主文")),
        )
    raise UnreadablePage(f"{url}: 不是解釋或判決頁")


def _fields(page: str) -> dict[str, HtmlElement]:
    """欄位名對到它的 `<li class="text">`。同名只留第一個。"""
    fields: dict[str, HtmlElement] = {}
    for li in parse_html(page).iter("li"):
        if "title" not in (li.get("class") or "").split():
            continue
        nxt = li.getnext()
        if nxt is None or nxt.tag != "li" or "text" not in (nxt.get("class") or "").split():
            continue
        fields.setdefault("".join((li.text_content() or "").split()), nxt)
    return fields


def _text(fragment: HtmlElement) -> str:
    return visible_text(tostring(fragment, encoding=str))


def _paragraphs(body: HtmlElement | None) -> str:
    if body is None:
        return ""
    paragraphs = [(pre.text_content() or "").replace("　", "").strip() for pre in body.iter("pre")]
    return "\n".join(p for p in paragraphs if p)
