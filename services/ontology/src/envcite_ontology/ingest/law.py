"""一部法規：把 `Laws` 陣列的一個元素解析成語料的形狀。

純函式，不碰資料庫。有問題時不丟例外：能保住的（整條文字、事件的原文）照樣保住，
問題記在 `problems`。`error` 使這部法規不可寫入（其他法規不受影響），`warning` 只是
待查。整批的驗收看比例，不看單一法規（`audit.py`）。
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, NamedTuple

from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import articles, commencement, history
from envcite_ontology.ingest.numerals import parse_yyyymmdd

#: 解析邏輯的版本。改變輸出的修改必須遞增，快照才會以新版重匯。
PARSER_VERSION = 3

_KINDS = {"憲法": "constitution", "法律": "statute", "命令": "regulation"}

# 中央法規標準法第 3 條的法律與命令名稱，加上資料中常見的非標準名稱。長的在前。
_DOCUMENT_TYPES = (
    "條例", "通則", "規程", "規則", "細則", "辦法", "綱要", "標準", "準則",
    "編制表", "憲法", "法", "律",
)  # fmt: skip
OTHER_DOCUMENT_TYPE = "其他"


@dataclass(frozen=True)
class ParsedUnit:
    """解析出的一個條文單元：路徑、類型、文字，與推導出的效力規則和區間。"""

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
    """一部解析過的法規：自然鍵、版本日、沿革事件、條文單元，與解析時發現的問題。"""

    # 法規的自然鍵；解析器產出什麼來源的法規就明寫什麼，沒有預設。
    jurisdiction: str
    id_scheme: str
    local_id: str
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
        """可寫入：沒有 `error` 等級的問題。"""
        return not any(p.severity == "error" for p in self.problems)


def document_type(name: str) -> str:
    """依法規名稱的結尾判定文件類型（條例、辦法…）；都不符為「其他」。"""
    return next((t for t in _DOCUMENT_TYPES if name.endswith(t)), OTHER_DOCUMENT_TYPE)


def parse(law: dict[str, Any], *, pcode: str, data_date: date) -> ParsedLaw:
    """把 `Laws` 陣列的一個元素解析成 `ParsedLaw`；問題記在 `problems`，不丟例外。

    `data_date` 是快照的資料日，沿革事件晚於它記為警告。
    """
    level = str(law.get("LawLevel", ""))
    title = str(law.get("LawName", "")).strip()
    problems = _header_problems(level, title)

    events = history.parse(str(law.get("LawHistories") or ""), statute=level in ("法律", "憲法"))
    problems += _event_problems(events, data_date)

    modified = parse_yyyymmdd(str(law.get("LawModifiedDate", "")))
    promulgations = [
        e for e in events if e.kind in history.PROMULGATION_KINDS and e.date is not None
    ]
    last = promulgations[-1] if promulgations else None
    version_date = last.date if last else modified
    problems += _version_date_problems(law, events, last, modified)

    abandoned_on, abandon_problems = _abandonment(law, events, modified)
    problems += abandon_problems

    parsed = ParsedLaw(
        jurisdiction="TW-CENTRAL",
        id_scheme="moj_pcode",
        local_id=pcode,
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


def _header_problems(level: str, title: str) -> list[Problem]:
    problems: list[Problem] = []
    if level not in _KINDS:
        problems.append(Problem("error", "law_level", level))
    if not title:
        problems.append(Problem("error", "title"))
    return problems


def _event_problems(events: list[history.Event], data_date: date) -> list[Problem]:
    problems: list[Problem] = []
    for e in events:
        if e.kind == "unknown":
            problems.append(Problem("warning", "event_unknown", e.text))
        if e.date is None:
            problems.append(Problem("warning", "event_undated", e.text))
        elif e.date > data_date:
            problems.append(Problem("warning", "event_after_data_date", e.text))
    return problems


def _version_date_problems(
    law: dict[str, Any],
    events: list[history.Event],
    last: history.Event | None,
    modified: date | None,
) -> list[Problem]:
    """版本日的來源有疑處者：沿革沒有可讀日期的公布時改取 `LawModifiedDate`。"""
    if last is None:
        if modified is None:
            return [
                Problem("error", "version_date", "沿革沒有可讀日期的公布，LawModifiedDate 也不可讀")
            ]
        return [
            Problem(
                "warning", "version_date_from_source", "沿革沒有可讀日期的公布，取 LawModifiedDate"
            )
        ]
    if modified not in {e.date for e in events}:
        return [
            Problem(
                "warning",
                "modified_date_mismatch",
                f"LawModifiedDate {law.get('LawModifiedDate')} 不是沿革中任何事件的日期",
            )
        ]
    return []


def _abandonment(
    law: dict[str, Any], events: list[history.Event], modified: date | None
) -> tuple[date | None, list[Problem]]:
    """廢止日，與廢止旗標跟沿革事件對不上的問題；沒有廢止旗標則無廢止日。"""
    if not str(law.get("LawAbandonNote", "")).strip():
        if any(e.kind == "repeal" for e in events):
            return None, [Problem("warning", "repeal_event_but_live")]
        return None, []
    problems: list[Problem] = []
    abandoned_on = _abandoned_on(events, modified)
    if abandoned_on is None:
        problems.append(Problem("error", "abandoned_date", "已廢止但無日期"))
    if not any(e.kind in ("repeal", "sunset") for e in events):
        problems.append(
            Problem(
                "warning", "abandoned_without_event", "廢止旗標無對應的沿革事件，取 LawModifiedDate"
            )
        )
    return abandoned_on, problems


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
    seen = _article_texts(parsed, law)
    if not seen:
        parsed.problems.append(Problem("warning", "no_article_text"))
        return

    scope = _scope(parsed, seen, version_date, last, note=note, effective=effective)
    effects: list[_Effect] = []
    for path, (_, content) in seen.items():
        result = articles.split(path, content)
        if result.degraded:
            parsed.degraded_articles += 1
            parsed.problems.append(
                Problem("warning", "article_degraded", f"{path}：{result.degraded}")
            )
        effect = _effect(path, scope)
        effects.append(effect)
        for u in result.units:
            parsed.units.append(
                ParsedUnit(
                    u.path,
                    u.type,
                    u.content,
                    articles.sort_key(u.path),
                    effect.kind,
                    effect.starts,
                    effect.ends,
                )
            )
    if stale := sum(e.stale for e in effects):
        parsed.problems.append(
            Problem("warning", "stale_last_article", f"{stale} 條：末條的施行日屬較早的公布")
        )
    if emptied := sum(e.emptied for e in effects):
        parsed.problems.append(
            Problem("warning", "empty_in_force", f"{emptied} 條的施行日不早於廢止日")
        )
    parsed.units.sort(key=lambda u: u.sort_key)


def _article_texts(parsed: ParsedLaw, law: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """可寫入的條：路徑 → (條號原文, 內容)。已刪除的條計數，認不得或重複的記為錯誤。"""
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
    return seen


@dataclass(frozen=True, slots=True)
class _Scope:
    """推導各條生效日時共用的依據：最後一次公布的範圍、施行說明與命令。"""

    version_date: date
    abandoned_on: date | None
    entry: str
    last_article: str
    last_article_touched: bool
    touched: set[str]
    deferred: set[str]
    from_note: commencement.Commencement | None
    orders: list[tuple[history.Event, set[str]]]


class _Effect(NamedTuple):
    """一條的效力區間，與推導過程中值得彙總的兩種狀況。"""

    kind: str
    starts: date | None
    ends: date | None
    stale: bool
    emptied: bool


def _touched(last: history.Event | None, seen: dict[str, tuple[str, str]]) -> set[str]:
    """最後一次公布所及的條。沿革沒有列出條號時範圍不明，視為全部：不把可能延後施行的條當成既有的。"""
    if last is None or last.kind in ("enactment", "full_amendment"):
        return set(seen)
    return commencement.touched_articles(last.text, seen) or set(seen)


def _scope(
    parsed: ParsedLaw,
    seen: dict[str, tuple[str, str]],
    version_date: date,
    last: history.Event | None,
    *,
    note: str,
    effective: date | None,
) -> _Scope:
    last_path = max(seen, key=articles.sort_key)
    touched = _touched(last, seen)

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
    return _Scope(
        version_date=version_date,
        abandoned_on=parsed.abandoned_on,
        entry=last.text if last else "",
        last_article=seen[last_path][1],
        last_article_touched=last_path in touched,
        touched=touched,
        deferred=deferred,
        from_note=from_note,
        orders=[(e, commencement.touched_articles(e.text, seen)) for e in after],
    )


def _effect(path: str, scope: _Scope) -> _Effect:
    rule = commencement.derive(
        version_date=scope.version_date,
        entry=scope.entry,
        last_article=scope.last_article,
        last_article_touched=scope.last_article_touched,
        deferred=path in scope.deferred,
        note=scope.from_note,
        touched=path in scope.touched,
    )
    stale = rule.rule == "stale"
    if stale:
        rule = commencement.Commencement("unconfirmed", None)
    rule = _settled(rule, path, scope)
    starts, ends = rule.start, scope.abandoned_on
    kind = rule.rule
    emptied = starts is not None and ends is not None and ends <= starts
    if emptied:
        starts = ends = None
        kind = "unconfirmed"
    if starts is None:
        ends = None
    return _Effect(kind, starts, ends, stale, emptied)


def _settled(
    rule: commencement.Commencement, path: str, scope: _Scope
) -> commencement.Commencement:
    """待命令定的規則換成命令所定；已廢止或溯及既往者改成對應的規則。"""
    if rule.rule == "by_order":
        rule = _by_order(path, scope.orders) or rule
    if rule.rule == "by_order" and scope.abandoned_on is not None:
        # 已廢止的法規不會「尚待命令施行」：是否施行過不可考。
        rule = commencement.Commencement("unconfirmed", None)
    if rule.rule == "specified" and rule.start is not None and rule.start < scope.version_date:
        # 明文規定溯及既往（多見於組織規程與補助辦法）：照寫，另成一種規則，
        # 綱要只准它早於版本日。
        rule = commencement.Commencement("retroactive", rule.start)
    return rule
