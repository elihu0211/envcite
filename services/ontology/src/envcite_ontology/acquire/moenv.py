"""環境部主管法規查詢系統（oaout.moenv.gov.tw/law）的行政函釋。

函釋沒有開放資料，只有網頁（02-01 §7）。系統沒有 robots.txt（該路徑回 404 頁）、頁面沒有使用條款；
環境部「網站資料開放宣告」（www.moenv.gov.tw/announcement/publicdeclare/913.html）以 CC BY 4.0
授權重製、改作與公開傳輸，**使用時須註明出處**，且不得惡意變更；函釋是公文，本身不得為著作權標的
（著作權法第 9 條第 1 項第 1 款）。本模組只解析，不寫資料庫。

走訪只用 GET：查詢頁列出父分類，父分類清單頁列出葉分類，**葉分類**以 `size=40&page=n` 分頁
（只翻父分類會漏項），每列有標題、字號、日期與「廢/停」標記及停止適用日期，依發文日新到舊排列；
詳細頁是一張欄位表（標題、發文機關、發文字號、發文日期、內容、相關法規）。內容是公文原文逐行
折行的轉錄，數字前後夾空白，解析時還原成段落。
"""

import html
import re
from dataclasses import dataclass
from datetime import date

from envcite_ontology.acquire.markup import visible_text
from envcite_ontology.ingest.numerals import CN_NUMERALS, parse_int

BASE = "https://oaout.moenv.gov.tw/law/"
QUERY = f"{BASE}ExecutiveQuery.aspx"
LIST = f"{BASE}ExecutiveList.aspx?feid={{feid}}&size=40&page={{page}}"
DETAIL = f"{BASE}ExecutiveData.aspx?id={{id}}&type=2"
ATTRIBUTION = "環境部主管法規查詢系統（環境部）；網站資料開放宣告（CC BY 4.0）"
#: 每頁上限；大於此值網站回空頁（02-01 §7.1）
PAGE_SIZE = 40

_FEID = re.compile(r'href="ExecutiveList\.aspx\?feid=(\d+)"')
_LEAVES = re.compile(r'<ul id="[^"]*_ulcid"[^>]*>(.*?)</ul>', re.S)
_ROW = re.compile(r"<tr>\s*<td>\d+\.</td>(.*?)</tr>", re.S)
_ROW_ID = re.compile(r'href="ExecutiveData\.aspx\?id=(\d+)&(?:amp;)?type=2"')
_ROW_FIELD = re.compile(r"<b>(字號|日期)：</b>\s*(?:<span>)?([^<]*)")
_ROW_CEASED = re.compile(r"停止適用日期：.*?<span>([^<]*)</span>", re.S)
_DOTTED_DATE = re.compile(r"^(\d{2,3})\.(\d{2})\.(\d{2})$")
_ROC_DATE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_FIELD = re.compile(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", re.S)
_TABLE = re.compile(r'<table class="table table-bordered tab-edit[^"]*">.*?</table>', re.S)
_RELATED = re.compile(r'<ul id="[^"]*_ulLegalDataRela"[^>]*>(.*?)</ul>', re.S)
_LINK_TEXT = re.compile(r"<a[^>]*>(.*?)</a>", re.S)
_SPACE = re.compile(r"(?<![A-Za-z0-9])\s+|\s+(?![A-Za-z0-9])")
_LABEL = re.compile(r"^(主旨|說明|正本|副本|附件)：")
_ENUMERATOR = re.compile(rf"^(?:[{CN_NUMERALS}]+、|[（(][{CN_NUMERALS}]+[）)]|\d+[、.．])")
_DOC_SUFFIX = re.compile(r"號(?:書函|函|令|公告)$")
_N = rf"(\d+|[{CN_NUMERALS}]+)"
_CITATION = re.compile(
    rf"^(?P<law>.+?)第{_N}(?:-(\d+))?條(?:之{_N})?(?:第{_N}項)?(?:第{_N}款)?(?:第{_N}目)?"
)


class UnreadablePage(ValueError):
    """頁面抓到了，卻不是預期的清單或函釋頁。"""


@dataclass(frozen=True, slots=True)
class Row:
    """葉分類清單上的一列。"""

    site_id: str
    document_no: str
    listed_on: date
    #: 列上有「廢/停」標記
    ceased: bool
    ceased_on: date | None

    @property
    def url(self) -> str:
        """這件函釋詳細頁的網址。"""
        return DETAIL.format(id=self.site_id)


@dataclass(frozen=True, slots=True)
class Letter:
    """環境部函釋詳細頁解析出的一件公文。"""

    title: str
    issuer: str
    document_no: str
    issued_on: date
    #: 主旨；原文沒有主旨（早年的函只有說明）時取標題。
    subject: str
    #: 說明各段，以換行相接；沒有說明時取主旨。
    body: str
    #: 「相關法規」欄的連結文字，照原文（如 `空氣污染防制法第32條第1項第1款`）
    related: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Citation:
    """一處法規引用拆出的法名與條文路徑。"""

    law_name: str
    #: 正規化的條文路徑（`art_32/para_1/pt_1`）；只寫法名、沒有條號為 None
    path: str | None


def normalize_document_no(no: str) -> str:
    """去除所有空白與字號後的文別（`…號函` → `…號`）：同一件公文在公報與網站寫法一致。"""
    return _DOC_SUFFIX.sub("號", "".join(no.split()))


def parents(query_page: str) -> list[str]:
    """查詢頁列出的父分類代碼（`feid`），依出現順序、去重。"""
    return list(dict.fromkeys(_FEID.findall(query_page)))


def leaves(parent_page: str) -> list[str]:
    """父分類頁所列的葉分類；沒有下層者自己就是葉。"""
    m = _LEAVES.search(parent_page)
    return list(dict.fromkeys(_FEID.findall(m.group(1)))) if m else []


def rows(listing: str) -> list[Row]:
    """葉分類清單頁的每一列；列上缺字號、日期或詳細頁連結時拋 `UnreadablePage`。"""
    found = []
    for m in _ROW.finditer(listing):
        cell = m.group(1)
        site_id = _ROW_ID.search(cell)
        fields = dict(_ROW_FIELD.findall(cell))
        if site_id is None or "字號" not in fields or "日期" not in fields:
            raise UnreadablePage(f"清單列不合：{_text(cell)[:80]}")
        ceased_on = _ROW_CEASED.search(cell)
        found.append(
            Row(
                site_id=site_id.group(1),
                document_no=normalize_document_no(html.unescape(fields["字號"])),
                listed_on=_dotted(fields["日期"]),
                ceased="label-fei" in cell,
                ceased_on=_dotted(ceased_on.group(1)) if ceased_on else None,
            )
        )
    return found


def content(page: str) -> str:
    """詳細頁的欄位表：快照只存這一段，頁面其餘部分（瀏覽人數、nonce）每次都不同。"""
    m = _TABLE.search(page)
    if m is None:
        raise UnreadablePage("沒有函釋欄位表")
    return m.group(0)


def parse(fragment: str) -> Letter:
    """解析詳細頁的欄位表（`content` 的輸出）。"""
    fields = {"".join(_text(k).split()).rstrip("：:"): v for k, v in _FIELD.findall(fragment)}
    try:
        title = _text(fields["標題"])
        issued = _ROC_DATE.search(_text(fields["發文日期"]))
        letter = fields["內容"]
        issuer, number = _text(fields["發文機關"]), _text(fields["發文字號"])
    except KeyError as e:
        raise UnreadablePage(f"缺欄位 {e}") from e
    if issued is None or not (title and issuer and number):
        raise UnreadablePage(f"欄位不完整：{title[:40]}")
    subject, body = _letter(letter)
    related = _RELATED.search(fields.get("相關法規", ""))
    return Letter(
        title=title,
        issuer=issuer,
        document_no=normalize_document_no(number),
        issued_on=_roc(issued),
        subject=subject or title,
        body=body,
        related=tuple(_text(t) for t in _LINK_TEXT.findall(related.group(1))) if related else (),
    )


_LISTED = re.compile(r"^(?P<law>.+?)第(?P<arts>\d+(?:-\d+)?(?:、\d+(?:-\d+)?)+)條$")


def expand(text: str) -> list[str]:
    """並列的條號各成一處：`水污染防治法第4、23條` → `水污染防治法第4條`、`水污染防治法第23條`。"""
    text = "".join(text.split())
    m = _LISTED.match(text)
    if m is None:
        return [text]
    return [f"{m.group('law')}第{a}條" for a in m.group("arts").split("、")]


def citation(text: str) -> Citation:
    """`空氣污染防制法第32條第1項第1款` → 法名與條文路徑；款是 `pt`、目是 `item`。

    條之幾有兩種寫法：`第63條之1` 與 `第63-1條`。
    """
    text = "".join(text.split())
    m = _CITATION.match(text)
    if m is None:
        return Citation(text, None)
    article, dashed, sub, para, point, item = (m.group(i) for i in range(2, 8))
    path = f"art_{_int(article)}" + (f"-{_int(dashed or sub)}" if dashed or sub else "")
    for prefix, n in (("para", para), ("pt", point), ("item", item)):
        if n is None:
            break
        path += f"/{prefix}_{_int(n)}"
    return Citation(m.group("law"), path)


def _letter(fragment: str) -> tuple[str, str]:
    """內容 → （主旨，說明）。主旨缺漏（早年的函只有說明）為空字串；正本、副本不屬內文。"""
    paragraphs: list[str] = []
    for line in _text(re.sub(r"<br\s*/?>", "\n", fragment), keep_lines=True).split("\n"):
        line = _SPACE.sub("", line)
        if not line:
            continue
        if not paragraphs or _LABEL.match(line) or _ENUMERATOR.match(line):
            paragraphs.append(line)
        else:
            paragraphs[-1] += line
    subject = ""
    body: list[str] = []
    for p in paragraphs:
        label = _LABEL.match(p)
        if label and label.group(1) == "主旨":
            subject = p[label.end() :]
        elif label and label.group(1) in ("正本", "副本"):
            continue
        else:
            body.append(p)
    return subject, "\n".join(body) or subject


def _text(fragment: str, *, keep_lines: bool = False) -> str:
    return visible_text(fragment, keep_lines=keep_lines)


def _dotted(text: str) -> date:
    m = _DOTTED_DATE.match(text.strip())
    if m is None:
        raise UnreadablePage(f"日期不合：{text!r}")
    year, month, day = (int(g) for g in m.groups())
    return date(year + 1911, month, day)


def _roc(m: re.Match[str]) -> date:
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError as e:
        raise UnreadablePage(f"日期不合：{m.group(0)}") from e


def _int(n: str) -> int:
    return int(n) if n.isdigit() else parse_int(n)
