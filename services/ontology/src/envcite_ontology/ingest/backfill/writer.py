"""寫入回推的歷史版本：一部法規一個交易。"""

from datetime import date
from typing import NamedTuple

from psycopg.types.range import Range

from envcite_ontology.db import Conn, fetch, scalar
from envcite_ontology.ingest.backfill.models import Anchor, Planned
from envcite_ontology.ingest.law import ParsedUnit
from envcite_ontology.ingest.write import add_unit_versions, content_sha, force_period


class _ExpressionRow(NamedTuple):
    id: int
    version_date: date
    title: str | None
    text_period: Range[date]
    authority: str
    status: str


class _VersionRow(NamedTuple):
    id: int
    path: str
    sha: bytes
    #: 起點未知（待確認、待命令）為 NULL
    force: Range[date] | None
    rule: str


def write(conn: Conn, anchor: Anchor, target: list[Planned], authority: str) -> str:
    """寫入一部法規的歷史版本；回傳動作：added、updated、unchanged、reshaped（不寫）。

    歷史版本是官方資料庫以外的所有版本，不論由哪個來源寫入：改由另一個來源（`authority`）提供時，
    同日的版本列改記新的來源、條文單元依新的文字取代。
    """
    existing = {
        e.version_date: e
        for e in fetch(
            conn,
            "SELECT id, version_date, title, text_period, text_authority::text, text_status::text"
            "  FROM corpus.legal_expression"
            " WHERE resource_id = %s AND language = 'zho' AND text_authority <> 'official_db'",
            (anchor.resource,),
            _ExpressionRow,
        )
    }
    if set(existing) - {v.version_date for v in target}:
        return "reshaped"

    changed = False
    expressions: dict[date, int] = {}
    for v in target:
        period = Range(v.version_date, v.until, "[)")
        if v.version_date in existing:
            known = existing[v.version_date]
            expression = known.id
            if (known.title, known.text_period, known.authority, known.status) != (
                v.title,
                period,
                authority,
                v.status,
            ):
                conn.execute(
                    "UPDATE corpus.legal_expression SET title = %s, text_period = %s,"
                    "  text_authority = %s, text_status = %s WHERE id = %s",
                    (v.title, period, authority, v.status, expression),
                )
                changed = True
        else:
            expression = scalar(
                conn,
                "INSERT INTO corpus.legal_expression (resource_id, language, version_date, title,"
                "  text_period, text_status, text_authority)"
                " VALUES (%s, 'zho', %s, %s, %s, %s, %s) RETURNING id",
                (anchor.resource, v.version_date, v.title, period, v.status, authority),
                int,
            )
        expressions[v.version_date] = expression

    ended: list[int] = []
    inserts: list[tuple[int, list[ParsedUnit]]] = []
    for v in target:
        expression = expressions[v.version_date]
        rows = {
            r.path: r
            for r in fetch(
                conn,
                "SELECT v.id, p.path, v.content_sha256, v.force_period, v.commencement_rule::text"
                "  FROM corpus.provision_version AS v"
                "  JOIN corpus.provision AS p ON p.id = v.provision_id"
                " WHERE v.expression_id = %s AND v.superseded_at IS NULL",
                (expression,),
                _VersionRow,
            )
        }
        wanted = {u.path: u for u in v.units}
        new = [
            u
            for p, u in wanted.items()
            if p not in rows
            or (rows[p].sha, rows[p].force, rows[p].rule)
            != (content_sha(u.content), force_period(u), u.rule)
        ]
        replaced = {u.path for u in new}
        ended += [r.id for p, r in rows.items() if p not in wanted or p in replaced]
        if new:
            inserts.append((expression, new))
    if ended:
        # 先結束再新增：同一單元相鄰兩版的區間同時移動時，不重疊約束只看現行列。
        conn.execute(
            "UPDATE corpus.provision_version SET superseded_at = now() WHERE id = ANY(%s)", (ended,)
        )
    for expression, units in inserts:
        add_unit_versions(conn, anchor.resource, expression, units, repeal=None)
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
