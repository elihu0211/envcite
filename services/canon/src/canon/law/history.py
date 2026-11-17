"""沿革解析。

`LawHistories` 是編號的多行文字，長句折行，連編號與文號的數字也可能被折斷：一筆以行首的
「（中華）民國…年…月…日」起頭，其餘行為續行，折行的換行與縮排直接去掉。

每筆解析出日期、文號與事件種類；認不得的記為 `unknown`、原文保留。管轄變更公告不影響
文字與效力，不是事件。

停止適用（`suspension`）與恢復適用（`resumption`）依中央法規標準法第 19 條：法規得暫停適用其
一部或全部，停止或恢復適用之程序準用廢止或制定之規定。只認兩種範圍：整部（發文機關與文號之後
只剩動作與起日），或列出的條；其他寫法（「關於國庫條文」「第 12 條但書」、附期限的暫停）範圍或
終點不明，記為 `unknown`。生效日依原文的起日；沒寫起日的停止適用準用第 22 條第 3 項，自公布或
發布之日起算至第三日；沒寫起日的恢復適用不推定第 13 條，同樣記為 `unknown`。
"""

import datetime
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from canon.law.commencement import PROMULGATION_DAYS, touched_articles
from canon.numerals import CN_NUMERALS, DATE_BARE, DATE_WITH_ERA, parse_int, roc_date

_ENTRY_START = re.compile(r"^\s*(?:[0-9]+\.\s*)?(?:中華)?民國")
_NUMBERING = re.compile(r"^[0-9]+\.\s*")
_WHITESPACE = re.compile(r"\s+")

# 制定、修正、公布、發布之後的「號令」「令」「函」與其文號。
_DOC_NO = re.compile(r"([^；，：。]*?字第[（(]?[0-9A-Za-z〇]*[)）]?[0-9]*號)")

PROMULGATION_KINDS = ("enactment", "full_amendment", "partial_amendment")


@dataclass(frozen=True)
class Event:
    """沿革裡的一筆事件：種類、日期、文號與原文；停止或恢復適用另有生效日。"""

    kind: str
    # 只有年月或格式不合者為 None：不以該月一日或任何日期頂替。
    date: date | None
    document_no: str | None
    text: str
    #: 停止或恢復適用的生效日；其他種類為 None。（欄位 `date` 遮蔽了型別名，這裡寫全名。）
    effective: datetime.date | None = None
    #: 列出的條（`art_N`）：部分修正所及，或停止、恢復適用所及。None 為整部或沒有列出條號。
    articles: frozenset[str] | None = None


def entries(histories: str) -> list[str]:
    """折行還原：回傳每筆沿革的單行文字（已去編號與空白）。"""
    out: list[str] = []
    for line in histories.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _ENTRY_START.match(line) or not out:
            out.append(line)
        else:
            out[-1] += line
    cleaned = (_WHITESPACE.sub("", _NUMBERING.sub("", e)) for e in out)
    return [e for e in cleaned if e]


def parse(histories: str, *, statute: bool = False, known: Iterable[str] = ()) -> list[Event]:
    """`statute`：法律（含憲法）的沿革；它們只由總統公布，見 `classify`。

    `known` 是現行條號（`art_N`），用來展開「第 24～26 條」這類範圍。
    """
    known = tuple(known)
    events: list[Event] = []
    for entry in entries(histories):
        m = DATE_WITH_ERA.match(entry)
        # 記載日期是句首那一個；內文裡的其他日期（如施行日）不是事件日期。
        when = roc_date(*m.groups()) if m else None
        kind, change = _classify(entry, when, statute=statute)
        if kind is None:
            continue
        doc = _DOC_NO.match(entry[m.end() :] if m else entry)
        effective, listed = (change.effective, change.listed) if change else (None, None)
        if kind == "partial_amendment":
            listed = entry
        articles = frozenset(touched_articles(listed, known)) if listed else None
        events.append(
            Event(kind, when, doc.group(1) if doc else None, entry, effective, articles or None)
        )
    # 沿革的第一筆就是制定：之前沒有別的公布。動詞不明（「令公布」「公布施行」）也是。
    if events and events[0].kind == "unknown" and _PROMULGATED.search(events[0].text):
        first = events[0]
        events[0] = Event("enactment", first.date, first.document_no, first.text)
    return events


_JURISDICTION = re.compile(r"公告.*(?:管轄|權責|改由|移由|職權)")
_FULL_TEXT = re.compile(r"全文")
_AMEND = re.compile(r"修正|增訂|刪除")
_ENACT = re.compile(r"制定|訂定|[公發]布同日施行|公布全文|發布全文")
# 施行命令：「令發布定自某日施行」「令定自某日生效」，或只有「施行」「生效」。
_COMMENCEMENT = re.compile(r"定[自於].*(?:施行|生效)|[公發]布.*自.*(?:施行|生效)")
_EFFECT = re.compile(r"施行|生效")
_BARE_COMMENCEMENT = re.compile(r"(?:自)?(?:同日|當日)?(?:起)?(?:施行|生效)")
# 公布或發布了某幾條的條文，沒有寫修正或增訂。
_PROMULGATED = re.compile(r"公布|發布")
_ARTICLES_PROMULGATED = re.compile(r"[公發]布.*第[0-9]+(?:-[0-9]+)?.*條條文")
_REPEAL_PART = re.compile(r"廢止第")
_REPEAL = re.compile(r"廢止")
_CORRECT = re.compile(r"更正|勘誤")

# 停止或恢復適用：同一分句裡「停止…適用」「暫停適用」「恢復…適用」，或「停用」。
_APPLICABILITY = re.compile(r"(?:停止|暫停|恢復)[^；。]{0,30}?適用|停用")
_N = f"[{CN_NUMERALS}]+"
# 起日：「自即日起」「自發布日」、民國日期，或「自本（76）年七月十五日起」（本年即發文那一年）。
_SINCE = (
    rf"(?:自)?(?:(?P<today>即日)|(?P<promulgated>[公發]布(?:之)?日)"
    rf"|本[（(][0-9]+[)）]年(?P<month>{_N})月(?P<day>{_N})日|(?P<on>{DATE_BARE.pattern}))(?:起)?"
)
_VERB = r"(?P<verb>(?:暫行)?停止適用|暫停適用|停用|恢復適用)"
_LIST = r"第(?P<list>[0-9]+(?:-[0-9]+)?(?:(?:[、，,～~及]|條?至)第?[0-9]+(?:-[0-9]+)?)*)條(?:條文)?"
# 整部：發文機關與文號之後只剩動作（令、函、公告、公布、發布、核准、備查…）、起日與動詞。
_WHOLE = re.compile(
    rf"(?:[令函]|公告|公布|發布)(?:准予核備|准予備查|核定發布|核准|發布|公布)?(?:{_SINCE})?{_VERB}$"
)
# 動作之前另有分句或條號：停止適用的對象可能是別的公文或條文，不是整部。
_NOT_ISSUER = re.compile(r"[，；。：「」]|第[0-9]+(?:-[0-9]+)?條")
# 列出的條：「第 24～26 條條文停用」，或「停止管理外匯條例第 7 條條文適用」。
_LISTED = re.compile(rf"{_LIST}(?:{_SINCE})?{_VERB}$")
_LISTED_OBJECT = re.compile(
    rf"(?:{_SINCE})?(?P<verb>停止|恢復)[^，；。：「」]{{0,20}}?{_LIST}(?:之)?適用$"
)


@dataclass(frozen=True)
class _Change:
    """停止或恢復適用的一筆：種類、生效日，與列出條號的那一段（整部為 None）。"""

    kind: str
    effective: date
    listed: str | None


def _after_issuer(text: str) -> str:
    """去掉發文機關與文號（到「令」「函」為止），剩下的是動作。"""
    return re.sub(r"^[^令函]*?[令函]", "", text)


def classify(entry: str, *, statute: bool = False) -> str | None:
    """事件種類；管轄變更公告回 None（不是事件），認不得回 'unknown'。

    法律是「公布」的；法律的沿革裡以「發布」起頭的動作是行政機關的施行命令（「令發布第 51-5 條條文
    定自…施行」「令發布：本次修正條文，定自…施行」），不是修正。一筆裡有修正也有停止適用時，
    種類是修正：附帶的停止適用說的是別的條文或公文，不是這部法規的另一個事件。
    """
    m = DATE_WITH_ERA.match(entry)
    return _classify(entry, roc_date(*m.groups()) if m else None, statute=statute)[0]


def _classify(entry: str, when: date | None, *, statute: bool) -> tuple[str | None, _Change | None]:
    m = DATE_WITH_ERA.match(entry)
    text = entry[m.end() :] if m else entry
    if statute and _after_issuer(text).lstrip("：:").startswith("發布") and _EFFECT.search(text):
        return "commencement", None

    if _JURISDICTION.search(text):
        return None, None
    if "失效" in text:
        return "sunset", None
    if _REPEAL.search(text) and not _REPEAL_PART.search(text) and not _AMEND.search(text):
        return "repeal", None
    if _AMEND.search(text):
        return ("full_amendment" if _FULL_TEXT.search(text) else "partial_amendment"), None
    if _ENACT.search(text):
        return "enactment", None
    if _APPLICABILITY.search(text):
        change = _change(text, when)
        return (change.kind, change) if change else ("unknown", None)
    if _ARTICLES_PROMULGATED.search(text):
        return "partial_amendment", None
    if _COMMENCEMENT.search(text) or _BARE_COMMENCEMENT.fullmatch(_after_issuer(text)):
        return "commencement", None
    if _CORRECT.search(text):
        return "correction", None
    return "unknown", None


def _change(text: str, when: date | None) -> _Change | None:
    """停止或恢復適用的範圍與生效日；範圍不是整部或列出的條、或生效日定不出來者為 None。"""
    compact = _WHITESPACE.sub("", text).rstrip("」』。")
    m = _WHOLE.search(compact)
    if m is not None and _NOT_ISSUER.search(compact[: m.start()]):
        m = None
    m = m or _LISTED.search(compact) or _LISTED_OBJECT.search(compact)
    if m is None:
        return None
    kind = "resumption" if m["verb"].startswith("恢復") else "suspension"
    effective = _effective(m, when, kind)
    if effective is None:
        return None
    listed = m.groupdict().get("list")
    return _Change(kind, effective, f"第{listed}條" if listed else None)


def _effective(m: re.Match[str], when: date | None, kind: str) -> date | None:
    if m["today"]:
        return when
    if m["promulgated"]:
        return when + timedelta(days=PROMULGATION_DAYS) if when else None
    if m["month"]:
        try:
            return date(when.year, parse_int(m["month"]), parse_int(m["day"])) if when else None
        except ValueError:
            return None
    if m["on"]:
        parts = DATE_BARE.fullmatch(m["on"])
        return roc_date(*parts.groups()) if parts else None
    # 停止適用準用廢止（第 22 條第 3 項）；恢復適用準用制定，原文沒寫起日就不推定第 13 條。
    if kind == "suspension" and when:
        return when + timedelta(days=PROMULGATION_DAYS)
    return None
