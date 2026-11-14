"""回推計畫（`plan`，純函式）：錨點往舊，對齊沿革、定文字與效力。"""

from datetime import date

from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest import history, law
from envcite_ontology.ingest.backfill.intervals import effect_intervals
from envcite_ontology.ingest.backfill.models import Anchor, Plan, Planned, Version
from envcite_ontology.ingest.backfill.texts import check_current, reuse_texts
from envcite_ontology.ingest.law import ParsedLaw

_LEVELS = {"constitution": "憲法", "statute": "法律", "regulation": "命令"}


def plan(anchor: Anchor, current: Version, past: list[Version]) -> Plan:
    """以錨點為終點往舊回推，算出要寫入的各歷史版本與沿途的問題。

    純函式。接不上的那一次公布與更早的每一次，寫成查不到全文的版本；
    有 `error` 問題（`Plan.ok` 為假）時不應寫入。
    """
    out = Plan()
    # 已廢止者的現行頁不帶最後一版的日期，最後一版在歷史清單裡。
    same_day = [v for v in (current, *past) if v.version_date == anchor.latest_date]
    latest = next((v for v in same_day if v.articles), same_day[0] if same_day else None)
    newest = max(v.version_date for v in (current, *past))
    if latest is not None:
        out.check = check_current(latest, anchor.latest_articles)
    elif newest < anchor.latest_date:
        # 來源還沒收最新的修正：回推只用錨點之前的版本，照樣可做，只是沒有現行版可核對。
        out.problems.append(
            Problem("warning", "source_behind", f"來源 {newest}，語料 {anchor.latest_date}")
        )
    else:
        out.problems.append(
            Problem("error", "current_date_mismatch", f"來源 {newest}，語料 {anchor.latest_date}")
        )
        return out

    statute = anchor.kind in ("statute", "constitution")
    events = [(e, history.parse(e, statute=statute)) for e in anchor.events]
    promulgated = sorted(
        {
            ev.date
            for _, parsed in events
            for ev in parsed
            if ev.kind in history.PROMULGATION_KINDS
            and ev.date is not None
            and ev.date < anchor.version_date
        },
        reverse=True,
    )
    by_date: dict[date, Version] = {}
    for v in past:
        if v.version_date in by_date:
            out.problems.append(Problem("error", "history_duplicate", str(v.version_date)))
            return out
        by_date[v.version_date] = v
    extra = sorted(d for d in by_date if d < anchor.version_date and d not in promulgated)
    if extra:
        out.problems.append(
            Problem("warning", "history_extra", "沿革沒有這些公布：" + "、".join(map(str, extra)))
        )

    stopped: date | None = None
    reference = anchor.articles
    # 來源裡與 `reference` 同一版的文字：兩個系統的同一條常差標點或字形（「，」與「；」、「椿」與
    # 「樁」），相鄰兩版是否修正，以同一來源的文字比，不以跨系統的文字比。
    newer = next(
        (v for v in (current, *past) if v.version_date == anchor.version_date and v.articles), None
    )
    accepted: list[tuple[Version, ParsedLaw]] = []
    until = anchor.version_date
    for when in promulgated:
        version = by_date.get(when)
        if version is None:
            out.problems.append(
                Problem("warning", "history_missing", f"{when} 沒有全文，更早的不寫")
            )
            stopped = when
            break
        if any(a.incomplete for a in version.articles):
            out.problems.append(
                Problem("warning", "history_incomplete", f"{when} 有條文只在 PDF，更早的不寫")
            )
            stopped = when
            break
        articles, ambiguous = reuse_texts(version, reference, newer)
        raw = {
            "LawLevel": _LEVELS[anchor.kind],
            "LawName": version.title,
            "LawModifiedDate": when.strftime("%Y%m%d"),
            "LawEffectiveDate": "",
            "LawEffectiveNote": "",
            "LawAbandonNote": "",
            "LawHistories": "\n".join(text for text, parsed in events if _before(parsed, until)),
            "LawArticles": [
                {"ArticleType": "A", "ArticleNo": no, "ArticleContent": text}
                for no, text in articles.items()
            ],
        }
        parsed = law.parse(raw, pcode=anchor.pcode, data_date=until)
        if not parsed.ok or parsed.version_date != when or not parsed.units:
            codes = "、".join(p.code for p in parsed.problems if p.severity == "error") or "無條文"
            out.problems.append(
                Problem("warning", "history_unparsable", f"{when}：{codes}，更早的不寫")
            )
            stopped = when
            break
        if ambiguous:
            out.problems.append(
                Problem("warning", "paragraph_ambiguous", f"{when}：{ambiguous} 處")
            )
        out.problems.extend(
            Problem(p.severity, p.code, f"{when}：{p.detail}")
            for p in parsed.problems
            if p.code == "stale_last_article"
        )
        out.parsed.append(parsed)
        accepted.append((version, parsed))
        reference = articles
        newer = version
        until = when

    out.versions = _unavailable(promulgated, stopped, until) + effect_intervals(anchor, accepted)
    never = sum(len(p.units) for _, p in accepted) - sum(len(v.units) for v in out.versions)
    if never:
        out.problems.append(Problem("warning", "never_in_force", f"{never} 個單元起點不早於下一版"))
    return out


def _unavailable(promulgated: list[date], stopped: date | None, until: date) -> list[Planned]:
    """停下處與更早的每一次公布：查不到全文的版本，舊到新，最新一版止於 `until`。"""
    out: list[Planned] = []
    for when in (d for d in promulgated if stopped is not None and d <= stopped):
        out.append(Planned(when, None, until, []))
        until = when
    out.reverse()
    return out


def _before(parsed: list[history.Event], until: date) -> bool:
    """這一筆沿革是否早於 `until`；沒有日期的不知先後，不給任何歷史版本用。"""
    return bool(parsed) and all(e.date is not None and e.date < until for e in parsed)
