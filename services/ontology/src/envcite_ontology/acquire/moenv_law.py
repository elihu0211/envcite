"""環境部主管法規查詢系統（oaout.moenv.gov.tw/law）的法規與歷史法規。

環境部主管之法律與命令，每次修正前的完整版本只在這裡（02-01 表 tbl:inventory、02-02 §3）：
全國法規資料庫的 API 只有現行版本，其網站的歷史頁 robots.txt 禁止。本系統沒有 robots.txt
（該路徑回 404 頁）、沒有開放資料或 API，只有 GET 可取的網頁；環境部「網站資料開放宣告」
（www.moenv.gov.tw/announcement/publicdeclare/913.html）以 CC BY 4.0 授權重製、改作與公開傳輸，
**使用時須註明出處**，且不得惡意變更。本模組只解析，不連網、不寫資料庫。

頁面形狀（解析只依這些，其餘忽略）：

- 分類：`LawCategoryMain.aspx?type=M` 列父分類，父分類頁列葉分類；葉分類清單以 `size=40&page=n`
  分頁（大於 40 回空頁），每列有日期、名稱連結（`LawContent.aspx?id=FL…|GL…`）與種類。
- 現行頁 `LawContent.aspx?id=`、歷史頁 `LawContentHistory.aspx?hid=&id=`：欄位表（法規名稱、
  公發布日、修正日期；歷史頁是「歷史法規：民國…」）與一個 `<pre>`。`<pre>` 內
  `<b>第 N 條</b>`（以編輯器改過的頁是 `<strong>`，行另包 `<span>`）起一條、
  `<b>…章…</b>` 是編章節，條與條之間空一行。
- 條文是**固定行寬的折行轉錄**：項以換行分隔，長句在行寬處硬折，款目的續行以懸掛縮排對齊，行首不得
  出現的標點（「。」「，」）折到下一行時獨佔一行。行寬因版本而異，以該頁最寬的行為準。
- 版面排不下的條，標題寫「（因條文排版無法完整呈現內容，請詳閱完整條文檔案）」，全文只在 PDF。
- 歷史清單 `LawContentHistoryList.aspx?id=`：每列一個 `hid` 與異動日期，新到舊。
"""

import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from envcite_ontology.acquire.markup import visible_text
from envcite_ontology.ingest.backfill import Article, UnreadablePage, Version
from envcite_ontology.ingest.numerals import CN_NUMERALS

BASE = "https://oaout.moenv.gov.tw/law/"
CATEGORIES = f"{BASE}LawCategoryMain.aspx?type=M"
LIST = f"{BASE}LawCategoryContentList.aspx?CategoryID={{category}}&size=40&page={{page}}"
CURRENT = f"{BASE}LawContent.aspx?id={{site_id}}"
HISTORY_LIST = f"{BASE}LawContentHistoryList.aspx?id={{site_id}}"
HISTORY = f"{BASE}LawContentHistory.aspx?hid={{hid}}&id={{site_id}}"
ATTRIBUTION = "環境部主管法規查詢系統（環境部）；網站資料開放宣告（CC BY 4.0）"
#: 每頁上限；大於此值網站回空頁
PAGE_SIZE = 40

#: 只收這兩種：行政規則與「實質法規」（公告）不是全國法規資料庫的法規。
KINDS = ("法律", "命令")

_PARENT = re.compile(r'href="/law/LawCategoryMain\.aspx\?type=M&(?:amp;)?CategoryID=(\d+)"')
_LEAF = re.compile(r'href="LawCategoryContentList\.aspx\?CategoryID=(\d+)"')
_ROW = re.compile(r"<tr>\s*<td[^>]*>\d+\.</td>(.*?)</tr>", re.S)
_ROW_ID = re.compile(r'href="LawContent\.aspx\?id=([A-Z]{2}[0-9]+)"[^>]*>([^<]*)</a>')
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_HISTORY_ROW = re.compile(r'href="LawContentHistory\.aspx\?hid=(\d+)&(?:amp;)?id=[A-Z0-9]+"')
_HISTORY_TITLE = re.compile(r'<div class="text-title">\s*<h2>(?:<i[^>]*></i>)?\s*歷史法規</h2>')
_DOTTED = re.compile(r"(\d{2,3})\.(\d{2})\.(\d{2})")
_ROC = re.compile(r"民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_FIELD = re.compile(
    r"<th[^>]*>\s*(?:<label>)?([^<：]+)：(?:</label>)?\s*</th>\s*<td[^>]*>(.*?)</td>", re.S
)
#: 名稱旁的標記（「廢」、英譯連結），不是名稱的一部分
_LABEL = re.compile(r'<span[^>]*class="label-[^"]*"[^>]*>.*?</span>', re.S)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S)
_BOLD = re.compile(r"^\s*<b>(.*?)</b>\s*$")
#: 條號與編章節標題的粗體：舊頁是 `<b>`，以編輯器改過的頁是 `<strong class=…>`
_STRONG = re.compile(r"<(/?)strong\b[^>]*>")
_TAG = re.compile(r"<[^>]+>")
_ARTICLE = re.compile(
    r"^第\s*([0-9]+(?:\s*-\s*[0-9]+)*)\s*條\s*(（因條文排版無法完整呈現內容[^）]*）)?$"
)
_ENUMERATOR = re.compile(
    rf"^[ 　]*(?:[{CN_NUMERALS}]+、|（[{CN_NUMERALS}]+）|\([{CN_NUMERALS}]+\)"
    r"|[0-9]+[.．、]|[（(][0-9]+[）)])"
)
#: 行首禁則：這些標點不會起一行，起行即是上一行的續行。
_CLOSING = "。，、；：）」』？！"
_BOX = re.compile(r"[─-╿]")
#: 行寬低於此值的頁沒有任何折行（全是短句）
_MIN_WRAP = 60


@dataclass(frozen=True, slots=True)
class Row:
    """葉分類清單上的一部法規。"""

    site_id: str
    title: str
    kind: str


def parent_categories(page: str) -> list[str]:
    """分類頁列出的父分類代碼（`CategoryID`），依出現順序、去重。"""
    return list(dict.fromkeys(_PARENT.findall(page)))


def leaf_categories(page: str) -> list[str]:
    """分類頁列出的葉分類代碼（`CategoryID`），依出現順序、去重。"""
    return list(dict.fromkeys(_LEAF.findall(page)))


def list_rows(page: str) -> list[Row]:
    """葉分類清單頁的每一部法規；列上沒有法規連結拋 `UnreadablePage`。"""
    rows: list[Row] = []
    for body in _ROW.findall(page):
        link = _ROW_ID.search(body)
        cells = [_text(c) for c in _CELL.findall(body)]
        if link is None or not cells:
            raise UnreadablePage("清單列沒有法規連結")
        rows.append(Row(link.group(1), _text(link.group(2)), cells[-1]))
    return rows


def history_ids(page: str) -> list[int]:
    """歷史清單的 hid，依頁面順序（新到舊）；從未修正的法規清單是空的（只有標題，沒有表）。"""
    if not _HISTORY_TITLE.search(page):
        raise UnreadablePage("不是歷史清單頁")
    return [int(h) for h in dict.fromkeys(_HISTORY_ROW.findall(page))]


def fragment(page: str) -> str:
    """現行頁或歷史頁中與內容有關的部分：欄位表到 `<pre>` 結束（沒有條文的頁到欄位表結束）。

    其餘部分（瀏覽人數、nonce）每次不同，存下來會使同一份內容的雜湊每次都變。
    """
    start = page.find('<div class="text-con">')
    if start < 0:
        raise UnreadablePage("沒有內容區")
    end = page.find("</pre>", start)
    if end < 0:
        end = page.find("</table>", start)
        if end < 0:
            raise UnreadablePage("內容區沒有欄位表")
        return page[start : end + len("</table>")]
    return page[start : end + len("</pre>")]


def parse_version(page: str) -> Version:
    """解析現行頁或歷史頁；版本日是歷史頁的「歷史法規」日，現行頁的修正日期（未曾修正為公發布日）。

    已廢止的法規，現行頁只有公發布日與廢止日期，最後一版列在歷史清單裡；沒有條文的頁（如編制表）
    條文為空。
    """
    fields = {k.strip(): v for k, v in _FIELD.findall(page)}
    title = _text(_LABEL.sub("", fields.get("法規名稱", "")))
    stamp = fields.get("歷史法規") or fields.get("修正日期") or fields.get("公發布日")
    when = _roc(_text(stamp)) if stamp else None
    if not title or when is None:
        raise UnreadablePage("沒有法規名稱或日期")
    pre = _PRE.search(page)
    return Version(title, when, _articles(pre.group(1)) if pre else [])


def _articles(pre: str) -> list[Article]:
    blocks: list[tuple[str, bool, list[str]]] = []
    for raw in _STRONG.sub(r"<\1b>", pre).split("\n"):
        bold = _BOLD.match(_TAG.sub(_keep_bold, raw))
        if bold:
            head = _text(bold.group(1))
            m = _ARTICLE.match(head)
            if m:
                blocks.append((f"第 {re.sub(r'\s+', '', m.group(1))} 條", bool(m.group(2)), []))
            else:
                blocks.append(("", False, []))  # 編章節：結束前一條
            continue
        line = html.unescape(_TAG.sub("", raw)).rstrip()
        if blocks and blocks[-1][0] and line.strip():
            blocks[-1][2].append(line)
    lines = [ln for _, _, body in blocks for ln in body if not _BOX.search(ln)]
    width = max((display_width(ln) for ln in lines), default=0)
    out = []
    for no, incomplete, body in blocks:
        if not no:
            continue
        content, ambiguous = unwrap(body, width)
        out.append(Article(no, content, incomplete, ambiguous))
    return out


def _keep_bold(m: re.Match[str]) -> str:
    return m.group(0) if m.group(0) in ("<b>", "</b>") else ""


def display_width(s: str) -> int:
    """字串的顯示欄寬：東亞寬字元（含寬度不定者）算 2，其餘算 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in s)


def unwrap(lines: list[str], width: int) -> tuple[str, int]:
    """把折行還原成項、款、目各一行；回傳（文字，判不準的分項處數）。

    一行接到上一行的情形：以行首禁則標點起行；懸掛縮排（縮排等於上一個款目編號的寬度）；上一行滿寬、
    這一行頂格且不是款目編號。滿寬而以「。」結束的行之後的頂格行判為新項（「但」起句者除外），這是
    來源本身不能分辨的地方，計入判不準。表格框線行與非懸掛的縮排行是版面，照原樣保留。
    """
    full = width - 2 if width >= _MIN_WRAP else None
    out: list[str] = []
    hang = 0  # 目前這一行（款目）的懸掛縮排寬度；0 為項
    last = ""  # 上一個實體行
    ambiguous = 0
    for line in lines:
        stripped = line.lstrip(" 　")
        indent = display_width(line) - display_width(stripped)
        boxed = bool(_BOX.search(line) or _BOX.search(last))
        enumerated = bool(_ENUMERATOR.match(line))
        join = False
        if out and not boxed and not enumerated:
            if stripped[:1] in _CLOSING:
                join = True
            elif indent:
                join = hang > 0 and indent == hang
            elif full is not None and display_width(last) >= full:
                if last.endswith("。") and not stripped.startswith("但"):
                    ambiguous += 1
                else:
                    join = True
        if join:
            out[-1] += stripped
        else:
            out.append(line)
            m = _ENUMERATOR.match(line)
            hang = display_width(m.group(0)) if m else 0
        last = line
    return "\n".join(out), ambiguous


def _roc(text: str) -> date | None:
    m = _ROC.search(text) or _DOTTED.search(text)
    if m is None:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def _text(fragment: str) -> str:
    return visible_text(fragment)
