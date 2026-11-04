"""立法院法律系統（lis.ly.gov.tw/lglawc）的法律與歷次版本。

法律每次修正前的完整版本，官方來源只有這裡（02-02 §3）：全國法規資料庫的 API 只有現行版本、
其網站的歷史頁 robots.txt 禁止，主管機關系統只有環境部的（`moenv_law`）。本系統由立法院國會圖書館
維護，收錄民國 59 年以來的立法沿革；沒有 robots.txt（404）、沒有使用條款頁，只有網頁、沒有 API。
法律條文依著作權法第 9 條第 1 項第 1 款不得為著作權之標的；立法院網站資料開放宣告
（www.ly.gov.tw/Pages/List.aspx?nodeid=10772，範圍寫的是立法院全球資訊網）以無償、不撤回的方式
授權重製與改作，**須註明出處**，本系統比照標示。本模組只解析，不連網、不寫資料庫。

頁面形狀（解析只依這些，其餘忽略）：

- 檢索是連線階段式：首頁 `lglawkm` 的表單（`action` 帶階段代碼、隱藏欄位 `INFO`）以 POST 送出
  法名稱（`_1_6_T`；比對範圍含名稱以外，同名者由呼叫端挑），結果頁的「現行法」表每列一個連結
  （`lawsingle?…`，法名稱的命中字另包 `<font>`）與通過、公布日期（民國 `YYYMMDD`）。
- 法律頁與版本頁同形：`law_NA` 是法名稱；「立法沿革」每一版一個小表，`version_0` 是三讀通過日
  與該版條文頁的連結，`version_1` 是公布日（另可有施行日列）；`class=date` 之後是條文：
  `<td id="partN">` 起一條、`<font color=C000FF>第N條</font>` 是條號，項款以 `<br>` 分行並以
  全形空白縮排；編章節是另一色的 `<font>`。
- 連結中的代碼每個連線階段不同，所以快照只存內容部分（`fragment`），版本以法律編號與通過日識別。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

from envcite_ontology.ingest.backfill import Article, UnreadablePage, Version
from envcite_ontology.ingest.numerals import CN_NUMERALS, parse_int

BASE = "https://lis.ly.gov.tw"
HOME = f"{BASE}/lglawc/lglawkm"
ATTRIBUTION = (
    "立法院法律系統（立法院國會圖書館）；法律條文不得為著作權之標的，"
    "比照立法院網站資料開放宣告註明出處"
)

_FORM = re.compile(r'<form[^>]*action="([^"]+)"')
_HIDDEN = re.compile(r"<input type=hidden name=([^ >]+) value=[\"']?([^\"' >]*)")
_HIT = re.compile(
    r"<td class=sumtd2_TI>\s*<a href=(/lglawc/lawsingle\?[^ >]+)[^>]*>(.*?)</a>.*?"
    r"<td class=sumtd2_PD>\s*(\d{7})\s*</td>\s*<td class=sumtd2_AD>\s*(\d{7})",
    re.S,
)
_ENTRY = re.compile(
    r"<td class=version_0><a href=(/lglawc/lawsingle\?[^ >]+)\s*>([^<]+)</a></td></tr>\s*"
    r"<tr><td class=version_1>([^<]+)<"
)
_KEY = re.compile(r"\^(\d{5})(\d{7})\d{2}\^")
_START = '<td class="law_NA">'
_END = 'class="R62"'
_TITLE = re.compile(r'<td class="law_NA">(.*?)<span', re.S)
_PART = re.compile(
    r'<td id="part\d+">\s*<font color=C000FF>([^<]+)</font>(.*?)</td></tr></table>', re.S
)
_ARTICLE_NO = re.compile(rf"^第([{CN_NUMERALS}]+)條((?:之[{CN_NUMERALS}]+)*)$")
_ROC = re.compile(r"民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_TAG = re.compile(r"<[^>]+>")
_SESSION = re.compile(r"(lawsingle\?)[0-9A-F]+")
_NOTE = re.compile(r"[（(][^（）()]*[）)]$")


@dataclass(frozen=True, slots=True)
class Hit:
    """檢索結果「現行法」表的一列。"""

    title: str
    link: str
    passed: date
    promulgated: date


@dataclass(frozen=True, slots=True)
class Entry:
    """立法沿革的一版：三讀通過日、公布日與條文頁。"""

    key: str  # 法律編號與通過日（民國 YYYMMDD），跨連線階段不變
    passed: date
    promulgated: date
    link: str


def name(title: str) -> str:
    """檢索用的法名稱：全國法規資料庫以結尾的括註分辨同名法（「（112.12.27制定）」），立法院沒有；
    括號在檢索式裡是語法，送出會被當成錯誤的檢索。"""
    return _NOTE.sub("", title).strip()


def search_form(home: str, title: str) -> tuple[str, dict[str, str]]:
    """首頁表單的送出位址與欄位：以法名稱檢索（欄位照首頁給的，改了站台不認）。"""
    action = _FORM.search(home)
    fields = dict(_HIDDEN.findall(home))
    if action is None or "INFO" not in fields:
        raise UnreadablePage("不是檢索首頁")
    fields.update({"_1_6_T": title, "_1_5_T": "", "_IMG_檢索.x": "1", "_IMG_檢索.y": "1"})
    return html.unescape(action.group(1)), fields


def hits(page: str) -> list[Hit]:
    """檢索結果的現行法；查無時（頁上是「※ 無檢索結果 ※」與首頁）是空的。"""
    if "無檢索結果" in page:
        return []
    if "檢索條件" not in page:
        raise UnreadablePage("不是檢索結果頁")
    return [
        Hit(_text(name).replace(" ", ""), link, _compact(passed), _compact(promulgated))
        for link, name, passed, promulgated in _HIT.findall(page)
    ]


def entries(page: str) -> list[Entry]:
    """立法沿革的各版，依頁面順序（舊到新）。"""
    out: list[Entry] = []
    for link, passed, promulgated in _ENTRY.findall(page):
        key = _KEY.search(link)
        when, out_on = _roc(passed), _roc(promulgated)
        if key is None or when is None or out_on is None or "公布" not in promulgated:
            raise UnreadablePage(f"立法沿革的一列讀不出：{_text(passed)}／{_text(promulgated)}")
        out.append(Entry(key.group(1) + key.group(2), when, out_on, link))
    if not out:
        raise UnreadablePage("沒有立法沿革")
    return out


def fragment(page: str) -> str:
    """法名稱到條文結束，條文裡連結（附件、立法理由）的連線階段代碼拿掉。

    其餘部分（表單）與這些代碼每個連線階段不同，存下來會使同一份內容的雜湊每次都變。
    """
    start = page.find(_START)
    end = page.find(_END, start)
    if start < 0 or end < 0:
        raise UnreadablePage("沒有法名稱或條文區")
    return _SESSION.sub(r"\1", page[start:end])


def parse_version(fragment: str, promulgated: date) -> Version:
    """一版的全文；版本日是公布日（沿革以公布日記，條文頁只有通過日）。"""
    title = _TITLE.search(fragment)
    if title is None or not _text(title.group(1)):
        raise UnreadablePage("沒有法名稱")
    articles: list[Article] = []
    for no, body in _PART.findall(fragment):
        m = _ARTICLE_NO.match(_text(no).replace(" ", ""))
        if m is None:
            raise UnreadablePage(f"條號讀不出：{_text(no)}")
        number = "-".join(str(parse_int(n)) for n in (m.group(1), *m.group(2).split("之")[1:]))
        lines = [_text(line) for line in re.split(r"<br\s*/?>", body)]
        articles.append(Article(f"第 {number} 條", "\n".join(ln for ln in lines if ln), False, 0))
    return Version(_text(title.group(1)), promulgated, articles)


def _compact(roc: str) -> date:
    return date(int(roc[:3]) + 1911, int(roc[3:5]), int(roc[5:]))


def _roc(text: str) -> date | None:
    m = _ROC.search(_text(text))
    if m is None:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())
