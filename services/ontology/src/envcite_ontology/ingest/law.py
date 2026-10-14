"""一部法規：把 `Laws` 陣列的一個元素解析成語料的形狀（envcite-docs 03-03 §5–§7）。

純函式，不碰資料庫。有問題時不丟例外：能保住的（整條文字、事件的原文）照樣保住，
問題記在 `problems`。`error` 使這部法規不可寫入（其他法規不受影響），`warning` 只是
待查。整批的驗收看比例，不看單一法規（`audit.py`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from envcite_ontology.ingest import articles, commencement, history
from envcite_ontology.ingest.numerals import parse_yyyymmdd

#: 解析邏輯的版本。改變輸出的修改必須遞增，快照才會以新版重匯。
PARSER_VERSION = 1

_KINDS = {"憲法": "constitution", "法律": "statute", "命令": "regulation"}

# 中央法規標準法第 3 條的法律與命令名稱，加上資料中常見的非標準名稱。長的在前。
_DOCUMENT_TYPES = (
    "條例", "通則", "規程", "規則", "細則", "辦法", "綱要", "標準", "準則",
    "編制表", "憲法", "法", "律",
)  # fmt: skip
OTHER_DOCUMENT_TYPE = "其他"


@dataclass(frozen=True)
class Problem:
    level: str  # error | warning
    code: str
    detail: str = ""


@dataclass(frozen=True)
class ParsedUnit:
    path: str
    type: str
    content: str | None
    sort_key: list[int]
    rule: str
    # 效力區間的起訖（含起、不含訖）；rule 為 by_order 或 unconfirmed 者兩端皆空。
    starts: date | None
    ends: date | None


@dataclass
class ParsedLaw:
    pcode: str
    kind: str
    document_type: str
    title: str
    version_date: date | None
    abandoned_on: date | None
    events: list[history.Event]
    # 生出現行版本的那一筆公布（events 的索引）；版本日取自來源欄位時為 None。
    version_event: int | None
    units: list[ParsedUnit] = field(default_factory=list)
    deleted_articles: int = 0
    degraded_articles: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.level == "error" for p in self.problems)


def document_type(name: str) -> str:
    return next((t for t in _DOCUMENT_TYPES if name.endswith(t)), OTHER_DOCUMENT_TYPE)


def parse(law: dict[str, Any], *, pcode: str, data_date: date) -> ParsedLaw:
    problems: list[Problem] = []

    def error(code: str, detail: str = "") -> None:
        problems.append(Problem("error", code, detail))

    def warn(code: str, detail: str = "") -> None:
        problems.append(Problem("warning", code, detail))

    level = str(law.get("LawLevel", ""))
    if level not in _KINDS:
        error("law_level", level)
    title = str(law.get("LawName", "")).strip()
    if not title:
        error("title")

    events = history.parse(str(law.get("LawHistories") or ""))
    for e in events:
        if e.kind == "unknown":
            warn("event_unknown", e.text)
        if e.date is None:
            warn("event_undated", e.text)
        elif e.date > data_date:
            warn("event_after_data_date", e.text)

    modified = parse_yyyymmdd(str(law.get("LawModifiedDate", "")))
    promulgations = [
        e for e in events if e.kind in history.PROMULGATION_KINDS and e.date is not None
    ]
    last = promulgations[-1] if promulgations else None
    version_date = last.date if last else modified
    if last is None:
        if modified is None:
            error("version_date", "沿革沒有可讀日期的公布，LawModifiedDate 也不可讀")
        else:
            warn("version_date_from_source", "沿革沒有可讀日期的公布，取 LawModifiedDate")
    elif modified not in {e.date for e in events}:
        warn(
            "modified_date_mismatch",
            f"LawModifiedDate {law.get('LawModifiedDate')} 不是沿革中任何事件的日期",
        )

    abandoned = bool(str(law.get("LawAbandonNote", "")).strip())
    abandoned_on = _abandoned_on(events, modified) if abandoned else None
    if abandoned and abandoned_on is None:
        error("abandoned_date", "已廢止但無日期")
    if abandoned and not any(e.kind in ("repeal", "sunset") for e in events):
        warn("abandoned_without_event", "廢止旗標無對應的沿革事件，取 LawModifiedDate")
    if not abandoned and any(e.kind == "repeal" for e in events):
        warn("repeal_event_but_live")

    parsed = ParsedLaw(
        pcode=pcode,
        kind=_KINDS.get(level, ""),
        document_type=document_type(title),
        title=title,
        version_date=version_date,
        abandoned_on=abandoned_on,
        events=events,
        version_event=next((i for i, e in enumerate(events) if e is last), None),
        problems=problems,
    )
    if version_date is not None:
        _articles(
            parsed,
            law,
            version_date,
            last,
            note=str(law.get("LawEffectiveNote") or ""),
            effective=parse_yyyymmdd(str(law.get("LawEffectiveDate") or "")),
        )
    return parsed


def _abandoned_on(events: list[history.Event], modified: date | None) -> date | None:
    """廢止日：沿革最早的廢止或失效事件；沒有時取來源的最後異動日。"""
    ends = sorted(
        (e.date, e.kind) for e in events if e.kind in ("repeal", "sunset") and e.date is not None
    )
    if not ends:
        return modified
    when, kind = ends[0]
    return when + timedelta(days=commencement.PROMULGATION_DAYS if kind == "repeal" else 0)


def _after(event: history.Event, last: history.Event) -> bool:
    return event.date is not None and last.date is not None and event.date >= last.date


def _by_order(
    path: str, orders: list[tuple[history.Event, set[str]]]
) -> commencement.Commencement | None:
    for event, listed in reversed(orders):
        if listed and path not in listed:
            continue
        when = commencement.ordered_start(event.text)
        if when is not None:
            return commencement.Commencement("specified", when)
    return None


def _articles(
    parsed: ParsedLaw,
    law: dict[str, Any],
    version_date: date,
    last: history.Event | None,
    *,
    note: str,
    effective: date | None,
) -> None:
    raw = [a for a in law.get("LawArticles") or [] if isinstance(a, dict)]

    seen: dict[str, tuple[str, str]] = {}
    for a in raw:
        if a.get("ArticleType") != "A":
            continue
        content = str(a.get("ArticleContent", ""))
        if articles.is_deleted(content):
            parsed.deleted_articles += 1
            continue
        path = articles.article_path(str(a.get("ArticleNo", "")))
        if path is None:
            parsed.problems.append(Problem("error", "article_no", str(a.get("ArticleNo"))))
            continue
        if path in seen:
            parsed.problems.append(Problem("error", "article_duplicate", path))
            continue
        seen[path] = (str(a.get("ArticleNo")), content)

    if not seen:
        parsed.problems.append(Problem("warning", "no_article_text"))
        return

    last_path = max(seen, key=articles.sort_key)
    last_text = seen[last_path][1]
    full_text = last is not None and last.kind in ("enactment", "full_amendment")
    # 沿革沒有列出條號時範圍不明，視為全部：不把可能延後施行的條當成既有的。
    touched = commencement.touched_articles(last.text, seen) if last and not full_text else set()
    if full_text or last is None or not touched:
        touched = set(seen)
    entry = last.text if last else ""

    # 施行說明所列的條待確認；說明沒有列出條號時，由施行日欄判定整次公布的施行日。
    deferred = commencement.touched_articles(note, seen) if note.strip() else set()
    from_note = (
        commencement.from_note(note, effective, version_date)
        if note.strip() and not deferred
        else None
    )

    # 最後一次公布之後、以命令定施行日者：命令列出條號的只及於那些條，沒有列的及於全部。
    after = [
        e for e in parsed.events if e.kind == "commencement" and (last is None or _after(e, last))
    ]
    orders = [(e, commencement.touched_articles(e.text, seen)) for e in after]
    emptied = 0
    for path, (_, content) in seen.items():
        result = articles.split(path, content)
        if result.degraded:
            parsed.degraded_articles += 1
            parsed.problems.append(
                Problem("warning", "article_degraded", f"{path}：{result.degraded}")
            )
        rule = commencement.derive(
            version_date=version_date,
            entry=entry,
            last_article=last_text,
            deferred=path in deferred,
            note=from_note,
            touched=path in touched,
        )
        if rule.rule == "by_order":
            rule = _by_order(path, orders) or rule
        if rule.rule == "by_order" and parsed.abandoned_on is not None:
            # 已廢止的法規不會「尚待命令施行」：是否施行過不可考。
            rule = commencement.Commencement("unconfirmed", None)
        starts, ends = rule.start, parsed.abandoned_on
        kind = rule.rule
        if starts is not None and ends is not None and ends <= starts:
            emptied += 1
            starts = ends = None
            kind = "unconfirmed"
        if starts is None:
            ends = None
        for u in result.units:
            parsed.units.append(
                ParsedUnit(u.path, u.type, u.content, articles.sort_key(u.path), kind, starts, ends)
            )
    if emptied:
        parsed.problems.append(
            Problem("warning", "empty_in_force", f"{emptied} 條的施行日不早於廢止日")
        )
    parsed.units.sort(key=lambda u: u.sort_key)
