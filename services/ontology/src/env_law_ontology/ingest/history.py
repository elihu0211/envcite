"""沿革解析（env-law-docs 03-03 §5）。

`LawHistories` 是編號的多行文字，長句折行，連編號與文號的數字也可能被折斷。一筆的
開頭是行首的「（中華）民國…年…月…日」，其餘行是它的續行；折行的換行與縮排直接去掉。

每筆解析出日期、文號與事件種類。認不得的寫法記為 `unknown`、原文保留，不猜。
管轄變更公告只說明主管機關改由誰管，不影響文字與效力，不是事件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from env_law_ontology.ingest.numerals import DATE_WITH_ERA, roc_date

_ENTRY_START = re.compile(r"^\s*(?:[0-9]+\.\s*)?(?:中華)?民國")
_NUMBERING = re.compile(r"^[0-9]+\.\s*")
_WHITESPACE = re.compile(r"\s+")

# 制定、修正、公布、發布之後的「號令」「令」「函」與其文號。
_DOC_NO = re.compile(r"([^；，：。]*?字第[（(]?[0-9A-Za-z〇]*[)）]?[0-9]*號)")

PROMULGATION_KINDS = ("enactment", "full_amendment", "partial_amendment")


@dataclass(frozen=True)
class Event:
    kind: str
    # 只有年月或格式不合者為 None：不以該月一日或任何日期頂替。
    date: date | None
    document_no: str | None
    text: str


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


def parse(histories: str) -> list[Event]:
    events: list[Event] = []
    for entry in entries(histories):
        kind = classify(entry)
        if kind is None:
            continue
        m = DATE_WITH_ERA.match(entry)
        # 記載日期是句首那一個；內文裡的其他日期（如施行日）不是事件日期。
        when = roc_date(*m.groups()) if m else None
        doc = _DOC_NO.match(entry[m.end() :] if m else entry)
        events.append(Event(kind, when, doc.group(1) if doc else None, entry))
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
_BARE_COMMENCEMENT = re.compile(r"(?:自)?(?:同日|當日)?(?:起)?(?:施行|生效)")
# 公布或發布了某幾條的條文，沒有寫修正或增訂。
_PROMULGATED = re.compile(r"公布|發布")
_ARTICLES_PROMULGATED = re.compile(r"[公發]布.*第[0-9]+(?:-[0-9]+)?.*條條文")
_REPEAL_PART = re.compile(r"廢止第")
_REPEAL = re.compile(r"廢止")
_CORRECT = re.compile(r"更正|勘誤")


def _after_issuer(text: str) -> str:
    """去掉發文機關與文號（到「令」「函」為止），剩下的是動作。"""
    return re.sub(r"^[^令函]*?[令函]", "", text)


def classify(entry: str) -> str | None:
    """事件種類；管轄變更公告回 None（不是事件），認不得回 'unknown'。"""
    m = DATE_WITH_ERA.match(entry)
    text = entry[m.end() :] if m else entry

    if _JURISDICTION.search(text):
        return None
    if "失效" in text:
        return "sunset"
    if "停止適用" in text or "停止管理" in text or "停用" in text:
        return "suspension"
    if _REPEAL.search(text) and not _REPEAL_PART.search(text) and not _AMEND.search(text):
        return "repeal"
    if _AMEND.search(text):
        if _FULL_TEXT.search(text):
            return "full_amendment"
        return "partial_amendment"
    if _ENACT.search(text):
        return "enactment"
    if _ARTICLES_PROMULGATED.search(text):
        return "partial_amendment"
    if _COMMENCEMENT.search(text) or _BARE_COMMENCEMENT.fullmatch(_after_issuer(text)):
        return "commencement"
    if _CORRECT.search(text):
        return "correction"
    return "unknown"
