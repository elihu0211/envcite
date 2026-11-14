"""從語料載入回推的錨點：這部法規最早的官方資料庫版本與其現況。"""

from datetime import date
from typing import NamedTuple

from psycopg.types.range import Range

from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.ingest.backfill.models import Anchor


class _AnchorRow(NamedTuple):
    resource: int
    kind: str
    repealed_on: date | None
    #: 沒有官方資料庫版本時為 NULL（`FILTER` 沒有符合的列）
    first: date | None
    latest: date


class _StartRow(NamedTuple):
    version_date: date
    path: str
    force: Range[date] | None


class _EventTextRow(NamedTuple):
    source_text: str


class _ArticleRow(NamedTuple):
    path: str
    #: 條本身沒有內容、下層也沒有內容時為 NULL
    text: str | None


def load_anchor(conn: Conn, pcode: str) -> Anchor | None:
    """語料裡 `pcode` 的現況；沒有官方資料庫版本時為 None。"""
    row = fetch_one(
        conn,
        "SELECT r.id, r.norm_kind::text, r.repealed_on,"
        "       min(e.version_date) FILTER (WHERE e.text_authority = 'official_db'),"
        "       max(e.version_date)"
        "  FROM corpus.legal_resource AS r"
        "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id AND e.language = 'zho'"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s"
        " GROUP BY r.id",
        (pcode,),
        _AnchorRow,
    )
    if row is None or row.first is None:
        return None
    first = row.first
    starts_by_version: dict[date, dict[str, date | None]] = {}
    for start in fetch(
        conn,
        "SELECT e.version_date, p.path, v.force_period FROM corpus.provision_version AS v"
        "  JOIN corpus.provision AS p ON p.id = v.provision_id"
        "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
        " WHERE e.resource_id = %s AND e.language = 'zho' AND e.text_authority = 'official_db'"
        "   AND v.superseded_at IS NULL",
        (row.resource,),
        _StartRow,
    ):
        starts_by_version.setdefault(start.version_date, {})[start.path] = (
            start.force.lower if start.force is not None else None
        )
    events = [
        event.source_text
        for event in fetch(
            conn,
            "SELECT source_text FROM corpus.lifecycle_event"
            " WHERE resource_id = %s AND source_text IS NOT NULL ORDER BY id",
            (row.resource,),
            _EventTextRow,
        )
    ]
    return Anchor(
        resource=row.resource,
        pcode=pcode,
        kind=row.kind,
        repealed_on=row.repealed_on,
        version_date=first,
        latest_date=row.latest,
        starts=starts_by_version.get(first, {}),
        after=[starts_by_version[d] for d in sorted(starts_by_version) if d > first],
        articles=_articles(conn, row.resource, first),
        latest_articles=_articles(conn, row.resource, row.latest),
        events=events,
    )


def _articles(conn: Conn, resource: int, version_date: date) -> dict[str, str]:
    return {
        f"第 {article.path.removeprefix('art_')} 條": article.text
        for article in fetch(
            conn,
            "SELECT p.path, corpus.article_text(v.id) FROM corpus.provision_version AS v"
            "  JOIN corpus.provision AS p ON p.id = v.provision_id"
            "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
            " WHERE e.resource_id = %s AND e.language = 'zho' AND e.version_date = %s"
            "   AND v.superseded_at IS NULL AND p.provision_type = 'article'",
            (resource, version_date),
            _ArticleRow,
        )
        if article.text is not None
    }
