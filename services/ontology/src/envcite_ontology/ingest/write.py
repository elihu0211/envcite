"""一部法規寫入語料。

動作由已存的版本與剛解析的版本比較決定：

- `added`：首次寫入，先前的版本與事件（舊版解析器的產出）整部取代；條文單元保留，
  錨在上面的標註不受影響。
- `versioned`：版本日晚於既有最新，新增一個版本；前一版的區間止於新版。
- `corrected`、`refreshed`：同一版本日。單元文字不同是更正，只有推導值（生效日、廢止日）
  不同是更新；皆新增列並結束舊列，不覆寫。下層單元更正連帶換掉所屬條的列（見 `_sync_current`）。
- `regressed`：版本日早於既有最新，不動語料。
- `unchanged`：解析結果與已存的一致（來源改的是本系統不用的欄位）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import cast

import psycopg
from psycopg.types.range import Range

from envcite_ontology.ingest.law import ParsedLaw, ParsedUnit

Conn = psycopg.Connection[tuple[object, ...]]

#: 條文單元版本的效力區間：[起, 訖)；兩端皆空為「待確認」或「待命令」。
Interval = Range[date] | None


@dataclass(frozen=True)
class Applied:
    action: str
    # 變動的單元數（corrected、refreshed），供警告的說明。
    changed: int = 0


def apply(conn: Conn, law: ParsedLaw, *, first_import: bool) -> Applied:
    assert law.version_date is not None
    resource = _upsert_resource(conn, law)
    latest = _latest_expression(conn, resource)
    if first_import or latest is None:
        _replace_all(conn, resource, law)
        result = Applied("added")
    else:
        expression, version_date = latest
        if law.version_date < version_date:
            return Applied("regressed")
        if law.version_date > version_date:
            _add_version(conn, resource, expression, law)
            result = Applied("versioned")
        else:
            result = _sync_current(conn, resource, expression, law)
    _set_repeal(conn, resource, law.abandoned_on)
    return result


def _interval(u: ParsedUnit, *, repeal: date | None = None) -> Interval:
    if u.starts is None:
        return None
    end = u.ends
    # 廢止生效日是各單元效力上界的來源。先收到這個上界，資源列的觸發器才放得過。
    if repeal is not None and u.starts < repeal and (end is None or end > repeal):
        end = repeal
    return Range(u.starts, end, "[)")


def _set_repeal(conn: Conn, resource: int, repeal: date | None) -> None:
    conn.execute(
        "UPDATE corpus.legal_resource SET repealed_on = %s"
        " WHERE id = %s AND repealed_on IS DISTINCT FROM %s",
        (repeal, resource, repeal),
    )


def _sha(content: str | None) -> bytes:
    return hashlib.sha256((content or "").encode("utf-8")).digest()


def _upsert_resource(conn: Conn, law: ParsedLaw) -> int:
    row = conn.execute(
        "INSERT INTO corpus.legal_resource"
        " (id_scheme, local_id, norm_kind, document_type, repealed_on)"
        " VALUES ('moj_pcode', %s, %s::corpus.norm_kind, %s, %s)"
        " ON CONFLICT (id_scheme, local_id) DO UPDATE"
        "   SET norm_kind = EXCLUDED.norm_kind, document_type = EXCLUDED.document_type"
        " RETURNING id",
        (law.pcode, law.kind, law.document_type, None),
    ).fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def _latest_expression(conn: Conn, resource: int) -> tuple[int, date] | None:
    row = conn.execute(
        "SELECT id, version_date FROM corpus.legal_expression"
        " WHERE resource_id = %s AND language = 'zho' ORDER BY version_date DESC LIMIT 1",
        (resource,),
    ).fetchone()
    if row is None:
        return None
    assert isinstance(row[0], int) and isinstance(row[1], date)
    return row[0], row[1]


def _insert_expression(conn: Conn, resource: int, law: ParsedLaw) -> int:
    row = conn.execute(
        "INSERT INTO corpus.legal_expression"
        " (resource_id, language, version_date, title, text_period, text_status, text_authority)"
        " VALUES (%s, 'zho', %s, %s, daterange(%s, NULL), %s::corpus.text_status, 'official_db')"
        " RETURNING id",
        (
            resource,
            law.version_date,
            law.title,
            law.version_date,
            "available" if law.units else "unavailable",
        ),
    ).fetchone()
    assert row is not None and isinstance(row[0], int)
    return row[0]


def _replace_events(conn: Conn, resource: int, law: ParsedLaw) -> None:
    """事件是沿革的純函式，整部取代；公布事件依日期連到版本日相同的版本。"""
    expressions = {
        d: i
        for i, d in conn.execute(
            "SELECT id, version_date FROM corpus.legal_expression WHERE resource_id = %s",
            (resource,),
        )
    }
    conn.execute("DELETE FROM corpus.lifecycle_event WHERE resource_id = %s", (resource,))
    promulgations = ("enactment", "full_amendment", "partial_amendment")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO corpus.lifecycle_event"
            " (resource_id, event_kind, event_date, document_no, expression_id, source_text)"
            " VALUES (%s, %s::corpus.event_kind, %s, %s, %s, %s)",
            [
                (
                    resource,
                    e.kind,
                    e.date,
                    e.document_no,
                    expressions.get(e.date) if e.kind in promulgations and e.date else None,
                    e.text,
                )
                for e in law.events
            ],
        )


def _ensure_provisions(conn: Conn, resource: int, units: list[ParsedUnit]) -> None:
    by_depth: dict[int, list[ParsedUnit]] = {}
    for u in units:
        by_depth.setdefault(u.path.count("/"), []).append(u)
    with conn.cursor() as cur:
        for depth in sorted(by_depth):
            cur.executemany(
                "INSERT INTO corpus.provision"
                " (resource_id, parent_id, provision_type, path)"
                " VALUES (%s,"
                "         (SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
                "         %s::corpus.provision_type, %s)"
                " ON CONFLICT (resource_id, path) DO NOTHING",
                [
                    (
                        resource,
                        resource,
                        u.path.rsplit("/", 1)[0] if depth else None,
                        u.type,
                        u.path,
                    )
                    for u in by_depth[depth]
                ],
            )


def _insert_versions(
    conn: Conn,
    resource: int,
    expression: int,
    units: list[ParsedUnit],
    *,
    repeal: date | None,
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO corpus.provision_version"
            " (provision_id, expression_id, resource_id, content, force_period, commencement_rule)"
            " VALUES ((SELECT id FROM corpus.provision WHERE resource_id = %s AND path = %s),"
            "         %s, %s, %s, %s, %s::corpus.commencement_rule)",
            [
                (
                    resource,
                    u.path,
                    expression,
                    resource,
                    u.content,
                    _interval(u, repeal=repeal),
                    u.rule,
                )
                for u in units
            ],
        )


def _replace_all(conn: Conn, resource: int, law: ParsedLaw) -> None:
    """版本與事件整部取代；條文單元保留。

    單元的身分是路徑，重建出來的是同一批路徑；刪掉它會連帶刪掉錨在上面的主張、驗證紀錄與
    行政規則的解釋，即使文字沒變。保留單元，文字相同的標註就重新錨上；新版沒有的路徑留著
    不礙事：沒有版本列的單元不出現在任何時點。
    """
    for table in ("lifecycle_event", "legal_expression"):  # 版本連帶刪除條文版本
        conn.execute(f"DELETE FROM corpus.{table} WHERE resource_id = %s", (resource,))
    expression = _insert_expression(conn, resource, law)
    _replace_events(conn, resource, law)
    _ensure_provisions(conn, resource, law.units)
    _insert_versions(conn, resource, expression, law.units, repeal=law.abandoned_on)


def _add_version(conn: Conn, resource: int, previous: int, law: ParsedLaw) -> None:
    assert law.version_date is not None
    conn.execute(
        "UPDATE corpus.legal_expression SET text_period = daterange(version_date, %s)"
        " WHERE id = %s",
        (law.version_date, previous),
    )
    _close_previous(conn, previous, law.units)
    expression = _insert_expression(conn, resource, law)
    _replace_events(conn, resource, law)
    _ensure_provisions(conn, resource, law.units)
    _insert_versions(conn, resource, expression, law.units, repeal=law.abandoned_on)


def _close_previous(conn: Conn, previous: int, units: list[ParsedUnit]) -> None:
    """前一版各單元的效力止於新版的起點（表 tbl:actions 的「新增版本」）。

    新版同路徑單元的起點已知者止於該起點；新版沒有該路徑者止於新版各單元已知起點的最小
    值；新版起點未知者不動，待起點確定時由 `_sync_current` 補上。
    """
    target = {u.path: _interval(u) for u in units}
    starts = [t.lower for t in target.values() if t is not None and t.lower is not None]
    law_start = min(starts) if starts else None

    rows = conn.execute(
        "SELECT v.id, p.path, v.force_period FROM corpus.provision_version AS v"
        " JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE v.expression_id = %s AND v.superseded_at IS NULL AND v.force_period IS NOT NULL",
        (previous,),
    ).fetchall()
    for version_id, path, in_force in rows:
        assert isinstance(in_force, Range) and in_force.lower is not None
        if path in target:
            new = target[path]
            if new is None or new.lower is None:
                continue
            end = new.lower
        elif law_start is not None:
            end = law_start
        else:
            continue
        if in_force.upper is not None and in_force.upper <= end:
            continue
        conn.execute(
            "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = %s",
            (version_id,),
        )
        if end > in_force.lower:
            conn.execute(
                "INSERT INTO corpus.provision_version"
                " (provision_id, expression_id, resource_id, content, force_period,"
                "  commencement_rule)"
                " SELECT provision_id, expression_id, resource_id, content, %s, commencement_rule"
                "   FROM corpus.provision_version WHERE id = %s",
                (Range(in_force.lower, end, "[)"), version_id),
            )
        # 新版的起點不晚於舊版的起點：舊版從未有效，只結束、不補新列。


def _sync_current(conn: Conn, resource: int, expression: int, law: ParsedLaw) -> Applied:
    current: dict[str, tuple[int, bytes, Interval, str]] = {}
    for version_id, path, sha, force_period, rule in conn.execute(
        "SELECT v.id, p.path, v.content_sha256, v.force_period, v.commencement_rule::text"
        "  FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        " WHERE v.expression_id = %s AND v.superseded_at IS NULL",
        (expression,),
    ):
        current[cast(str, path)] = (
            cast(int, version_id),
            cast(bytes, sha),
            cast(Interval, force_period),
            cast(str, rule),
        )
    parsed = {u.path: u for u in law.units}

    text_changed = [
        p for p in parsed if p not in current or current[p][1] != _sha(parsed[p].content)
    ] + [p for p in current if p not in parsed]
    derived_changed = [
        p
        for p, u in parsed.items()
        if p in current
        and p not in text_changed
        and (current[p][2] != _interval(u, repeal=law.abandoned_on) or current[p][3] != u.rule)
    ]

    previous = conn.execute(
        "SELECT id FROM corpus.legal_expression"
        " WHERE resource_id = %s AND language = 'zho' AND version_date < %s"
        " ORDER BY version_date DESC LIMIT 1",
        (resource, law.version_date),
    ).fetchone()
    if previous is not None:
        assert isinstance(previous[0], int)
        _close_previous(conn, previous[0], law.units)

    # 條自己的內容是空值，雜湊不會因下層更正而變，故下層改變時條的列也換新。原為舊檢索索引
    # （以條的列為鍵）而設，索引改為逐段落內容定址（`retrieval.lexical_index`）後暫時保留。
    article_refreshed = sorted(
        {p.split("/", 1)[0] for p in text_changed if "/" in p} - {*text_changed, *derived_changed}
    )

    if text_changed or derived_changed:
        touched = (*text_changed, *derived_changed, *article_refreshed)
        replace = [p for p in touched if p in parsed]
        ended = [current[p][0] for p in touched if p in current]
        if ended:
            conn.execute(
                "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = ANY(%s)",
                (ended,),
            )
        _ensure_provisions(conn, resource, [parsed[p] for p in replace])
        _insert_versions(
            conn, resource, expression, [parsed[p] for p in replace], repeal=law.abandoned_on
        )

    conn.execute(
        "UPDATE corpus.legal_expression SET title = %s WHERE id = %s AND title <> %s",
        (law.title, expression, law.title),
    )
    _replace_events(conn, resource, law)

    if text_changed:
        return Applied("corrected", len(text_changed))
    if derived_changed:
        return Applied("refreshed", len(derived_changed))
    return Applied("unchanged")
