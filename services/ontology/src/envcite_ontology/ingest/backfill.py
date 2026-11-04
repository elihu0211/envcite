"""歷史版本：主管機關系統的完整歷次版本 → 條文單元版本（02-02 §3）。

只補**首次取得全國法規資料庫快照之前**的版本：語料裡該法規最早的官方資料庫版本（錨點）是終點，由它
往舊逐一回推，接得上才寫（`plan`，純函式）：

1. **對齊沿革**：錨點之前沿革的每一次公布（制定、全文修正、部分修正）都要有一個同日的歷史版本；
   缺一次、該版有條文只在 PDF、或解析不出可寫的版本，就停在那裡：那一次與更早的每一次公布寫成
   **查不到全文的版本**（`text_status = unavailable`，沒有條文單元版本、標題不詳），版本的時間軸照樣
   接到制定、不留縫；不跳過缺口接著寫，也不以其他版本的文字頂替。
2. **文字**：與較新版本正規化後相同的條，沿用較新版本的文字與分項（錨點是全國法規資料庫
   的文字），只有改過的條用主管機關系統的轉錄；轉錄的折行還原有判不準的分項處，逐版計數記警告。
3. **效力**：每一版當作「那時的現行版本」交給 `law.parse` 推導生效日——沿革截到下一次公布之前，
   所以施行命令只及於它所屬的那一版。各單元的效力止於較新版本同路徑單元的起點；較新版本沒有該路徑
   （條被刪）者止於那一版的法規層起點（各條已知起點的最小值），與增量匯入的規則相同（02-03 §8）。
   較新版本起點未知者往更新的版本找；起點不早於下一版者從未生效，不寫列。

寫入（`write`）一部法規一個交易：版本列與目標相同者不動，不同者以新列取代舊列（記錄時間只增不改）；
已寫入的歷史版本日不在這次的目標裡（來源的歷次版本改變了），整部不寫，記 error 待人查看。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from typing import cast

import psycopg
from psycopg.types.range import Range

from envcite_ontology.ingest import history, law
from envcite_ontology.ingest.law import ParsedLaw, ParsedUnit, Problem
from envcite_ontology.ingest.write import _ensure_provisions, _insert_versions, _sha

Conn = psycopg.Connection[tuple[object, ...]]

_LEVELS = {"constitution": "憲法", "statute": "法律", "regulation": "命令"}


class UnreadablePage(ValueError):
    """來源的頁面抓到了，卻不是預期的形狀。"""


@dataclass(frozen=True, slots=True)
class Article:
    article_no: str  # 「第 N 條」「第 N-M 條」
    #: 項、款、目各一行
    content: str
    #: 版面排不下、全文只在附件（PDF）
    incomplete: bool
    #: 轉錄還原時判不準的分項處（來源是折行轉錄者才有）
    ambiguous: int


@dataclass(frozen=True, slots=True)
class Version:
    """來源的一個歷次版本；版本日是公布或發布日，與沿革同。"""

    title: str
    version_date: date
    articles: list[Article]


def fold(text: str) -> str:
    """比對用：相容字形與空白不算差異（各系統的括號全半形、英數前後的空白不同）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class Anchor:
    """語料裡這部法規的現況：回推的終點。"""

    resource: int
    pcode: str
    kind: str
    repealed_on: date | None
    #: 最早的官方資料庫版本
    version_date: date
    #: 最新版本（主管機關系統的現行頁要與它同日）
    latest_date: date
    #: 錨點各單元現行列的效力起點（未知為 None）
    starts: dict[str, date | None]
    #: 錨點各條的全文（條號 `第 N 條` → 文字）
    articles: dict[str, str]
    #: 最新版本各條的全文，用來核對還原的轉錄
    latest_articles: dict[str, str]
    #: 沿革（`corpus.lifecycle_event` 的原文，依原順序）
    events: list[str]


@dataclass(frozen=True)
class Planned:
    version_date: date
    #: 查不到全文的版本（`units` 為空）不知道當時的標題
    title: str | None
    #: 文字時間的上界：下一個版本的版本日
    until: date
    units: list[ParsedUnit]

    @property
    def status(self) -> str:
        return "available" if self.title is not None else "unavailable"


@dataclass
class Plan:
    versions: list[Planned] = field(default_factory=list)  # 舊到新
    problems: list[Problem] = field(default_factory=list)
    #: 每一版當作現行版解析的結果，供整批驗收
    parsed: list[ParsedLaw] = field(default_factory=list)
    #: 現行頁與全國法規資料庫現行文字的核對：
    #: exact、typography、structure、differs、incomplete、missing
    check: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not any(p.level == "error" for p in self.problems)


def plan(anchor: Anchor, current: Version, past: list[Version]) -> Plan:
    out = Plan()
    # 已廢止者的現行頁不帶最後一版的日期，最後一版在歷史清單裡。
    same_day = [v for v in (current, *past) if v.version_date == anchor.latest_date]
    latest = next((v for v in same_day if v.articles), same_day[0] if same_day else None)
    newest = max(v.version_date for v in (current, *past))
    if latest is not None:
        out.check = _check(latest, anchor.latest_articles)
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
        articles, ambiguous = _texts(version, reference, newer)
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
            codes = "、".join(p.code for p in parsed.problems if p.level == "error") or "無條文"
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
            Problem(p.level, p.code, f"{when}：{p.detail}")
            for p in parsed.problems
            if p.code == "stale_last_article"
        )
        out.parsed.append(parsed)
        accepted.append((version, parsed))
        reference = articles
        newer = version
        until = when

    out.versions = _unavailable(promulgated, stopped, until) + _intervals(anchor, accepted)
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


def _texts(
    version: Version, reference: dict[str, str], newer: Version | None
) -> tuple[dict[str, str], int]:
    """各條的文字：與較新一版相同（正規化後，或與來源的較新一版相同）的沿用較新一版的文字。"""
    known = {fold(t): t for t in reference.values()}
    if newer is not None:
        known |= {
            fold(a.content): reference[a.article_no]
            for a in newer.articles
            if a.article_no in reference and fold(a.content) not in known
        }
    texts: dict[str, str] = {}
    ambiguous = 0
    for a in version.articles:
        same = known.get(fold(a.content))
        texts[a.article_no] = same if same is not None else a.content
        ambiguous += a.ambiguous if same is None else 0
    return texts, ambiguous


def _check(current: Version, latest: dict[str, str]) -> Counter[str]:
    got = {a.article_no: a for a in current.articles}
    out: Counter[str] = Counter()
    for no, text in latest.items():
        a = got.get(no)
        if a is None:
            out["missing"] += 1
        elif a.incomplete:
            out["incomplete"] += 1
        elif a.content == text:
            out["exact"] += 1
        elif fold(a.content) != fold(text):
            out["differs"] += 1
        elif a.content.count("\n") == text.count("\n"):
            out["typography"] += 1
        else:
            out["structure"] += 1
    return out


@dataclass
class _Later:
    starts: dict[str, date | None]
    #: 從未生效、不寫列的路徑：較舊的版本越過它往更新的找
    skipped: set[str]

    @property
    def law_start(self) -> date | None:
        known = [s for s in self.starts.values() if s is not None]
        return min(known) if known else None


def _end(later: list[_Later], path: str) -> date | None:
    for v in later:
        if path in v.skipped:
            continue
        if path in v.starts:
            if v.starts[path] is not None:
                return v.starts[path]
            continue
        if v.law_start is not None:
            return v.law_start
    return None


def _intervals(anchor: Anchor, accepted: list[tuple[Version, ParsedLaw]]) -> list[Planned]:
    """由新往舊定每一版各單元的效力上界（`accepted` 是新到舊）。"""
    later = [_Later(dict(anchor.starts), set())]
    planned: list[Planned] = []
    until = anchor.version_date
    for version, parsed in accepted:
        units: list[ParsedUnit] = []
        starts: dict[str, date | None] = {}
        skipped: set[str] = set()
        for u in parsed.units:
            if u.starts is None:
                units.append(u)
                starts[u.path] = None
                continue
            end = _end(later, u.path)
            if anchor.repealed_on is not None and (end is None or end > anchor.repealed_on):
                end = anchor.repealed_on
            if end is not None and end <= u.starts:
                skipped.add(u.path)
                continue
            units.append(replace(u, ends=end))
            starts[u.path] = u.starts
        later.insert(0, _Later(starts, skipped))
        assert parsed.version_date is not None
        planned.append(Planned(parsed.version_date, version.title, until, units))
        until = parsed.version_date
    planned.reverse()
    return planned


def load_anchor(conn: Conn, pcode: str) -> Anchor | None:
    """語料裡 `pcode` 的現況；沒有官方資料庫版本時為 None。"""
    row = conn.execute(
        "SELECT r.id, r.norm_kind::text, r.repealed_on,"
        "       min(e.version_date) FILTER (WHERE e.text_authority = 'official_db'),"
        "       max(e.version_date)"
        "  FROM corpus.legal_resource AS r"
        "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id AND e.language = 'zho'"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s"
        " GROUP BY r.id",
        (pcode,),
    ).fetchone()
    if row is None or row[3] is None:
        return None
    resource, kind, repealed_on, first, latest = row
    assert isinstance(resource, int) and isinstance(first, date) and isinstance(latest, date)
    starts: dict[str, date | None] = {}
    for path, force in conn.execute(
        "SELECT p.path, v.force_period FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
        " WHERE e.resource_id = %s AND e.language = 'zho' AND e.version_date = %s"
        "   AND v.superseded_at IS NULL",
        (resource, first),
    ):
        starts[cast(str, path)] = cast(Range[date], force).lower if force is not None else None
    events = [
        cast(str, text)
        for (text,) in conn.execute(
            "SELECT source_text FROM corpus.lifecycle_event"
            " WHERE resource_id = %s AND source_text IS NOT NULL ORDER BY id",
            (resource,),
        )
    ]
    return Anchor(
        resource=resource,
        pcode=pcode,
        kind=str(kind),
        repealed_on=cast(date | None, repealed_on),
        version_date=first,
        latest_date=latest,
        starts=starts,
        articles=_articles(conn, resource, first),
        latest_articles=_articles(conn, resource, latest),
        events=events,
    )


def _articles(conn: Conn, resource: int, version_date: date) -> dict[str, str]:
    return {
        f"第 {cast(str, path).removeprefix('art_')} 條": cast(str, text)
        for path, text in conn.execute(
            "SELECT p.path, corpus.article_text(v.id) FROM corpus.provision_version AS v"
            "  JOIN corpus.provision AS p ON p.id = v.provision_id"
            "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
            " WHERE e.resource_id = %s AND e.language = 'zho' AND e.version_date = %s"
            "   AND v.superseded_at IS NULL AND p.provision_type = 'article'",
            (resource, version_date),
        )
        if text is not None
    }


def write(conn: Conn, anchor: Anchor, target: list[Planned], authority: str) -> str:
    """寫入一部法規的歷史版本；回傳動作：added、updated、unchanged、reshaped（不寫）。

    歷史版本是官方資料庫以外的所有版本，不論由哪個來源寫入：改由另一個來源（`authority`）提供時，
    同日的版本列改記新的來源、條文單元依新的文字取代。
    """
    existing = {
        cast(date, d): (
            cast(int, i),
            cast(str | None, t),
            cast(Range[date], p),
            cast(str, a),
            cast(str, st),
        )
        for i, d, t, p, a, st in conn.execute(
            "SELECT id, version_date, title, text_period, text_authority::text, text_status::text"
            "  FROM corpus.legal_expression"
            " WHERE resource_id = %s AND language = 'zho' AND text_authority <> 'official_db'",
            (anchor.resource,),
        )
    }
    if set(existing) - {v.version_date for v in target}:
        return "reshaped"

    changed = False
    expressions: dict[date, int] = {}
    for v in target:
        period = Range(v.version_date, v.until, "[)")
        if v.version_date in existing:
            expression, title, current, by, was = existing[v.version_date]
            if (title, current, by, was) != (v.title, period, authority, v.status):
                conn.execute(
                    "UPDATE corpus.legal_expression SET title = %s, text_period = %s,"
                    "  text_authority = %s, text_status = %s WHERE id = %s",
                    (v.title, period, authority, v.status, expression),
                )
                changed = True
        else:
            row = conn.execute(
                "INSERT INTO corpus.legal_expression (resource_id, language, version_date, title,"
                "  text_period, text_status, text_authority)"
                " VALUES (%s, 'zho', %s, %s, %s, %s, %s) RETURNING id",
                (anchor.resource, v.version_date, v.title, period, v.status, authority),
            ).fetchone()
            assert row is not None and isinstance(row[0], int)
            expression = row[0]
        expressions[v.version_date] = expression

    ended: list[int] = []
    inserts: list[tuple[int, list[ParsedUnit]]] = []
    for v in target:
        expression = expressions[v.version_date]
        rows = {
            cast(str, path): (cast(int, i), cast(bytes, sha), force, cast(str, rule))
            for i, path, sha, force, rule in conn.execute(
                "SELECT v.id, p.path, v.content_sha256, v.force_period, v.commencement_rule::text"
                "  FROM corpus.provision_version AS v"
                "  JOIN corpus.provision AS p ON p.id = v.provision_id"
                " WHERE v.expression_id = %s AND v.superseded_at IS NULL",
                (expression,),
            )
        }
        wanted = {u.path: u for u in v.units}
        new = [
            u
            for p, u in wanted.items()
            if p not in rows or rows[p][1:] != (_sha(u.content), _range(u), u.rule)
        ]
        replaced = {u.path for u in new}
        ended += [i for p, (i, *_) in rows.items() if p not in wanted or p in replaced]
        if new:
            inserts.append((expression, new))
    if ended:
        # 先結束再新增：同一單元相鄰兩版的區間同時移動時，不重疊約束只看現行列。
        conn.execute(
            "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = ANY(%s)", (ended,)
        )
    for expression, units in inserts:
        _ensure_provisions(conn, anchor.resource, units)
        _insert_versions(conn, anchor.resource, expression, units, repeal=None)
    conn.execute(
        "UPDATE corpus.lifecycle_event AS ev SET expression_id = e.id"
        "  FROM corpus.legal_expression AS e"
        " WHERE ev.resource_id = %(r)s AND ev.expression_id IS NULL"
        "   AND ev.event_kind IN ('enactment', 'full_amendment', 'partial_amendment')"
        "   AND e.resource_id = %(r)s AND e.language = 'zho' AND e.version_date = ev.event_date",
        {"r": anchor.resource},
    )
    if not existing:
        return "added" if target else "unchanged"
    return "updated" if changed or ended or inserts else "unchanged"


def _range(u: ParsedUnit) -> Range[date] | None:
    return None if u.starts is None else Range(u.starts, u.ends, "[)")
