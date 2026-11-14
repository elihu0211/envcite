"""法規與條文的查詢：版本解析、閱讀順序、IRI、條文全文與違憲失效都由 SQL 決定，這裡不重算。"""

import re
from datetime import date
from functools import cached_property
from typing import NamedTuple

from envcite_ontology.db import fetch, fetch_one
from envcite_ontology.store.params import as_of_param, expiry, time_left
from envcite_ontology.store.ports import ConnectionSource
from envcite_ontology.store.records import (
    LANGUAGE,
    ArticleDetailRecord,
    ArticleRecord,
    InvalidationRecord,
    LawKey,
    LawRecord,
    LawSummaryRecord,
    StructureHeadingRecord,
    is_valid_law_id,
)

#: 某日有效、有全文的版本；法規不存在、該日無版本或該日的版本查不到全文（`text_status`）皆查無結果。
_RESOLVE = """
    SELECT r.id, corpus.resource_iri(r.norm_kind, r.local_id), e.title, e.language,
           (SELECT min(le.event_date)
              FROM corpus.lifecycle_event AS le
             WHERE le.resource_id = r.id AND le.event_kind = 'enactment'),
           r.jurisdiction, r.id_scheme, r.local_id
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
     WHERE r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction
       AND r.id_scheme = %(id_scheme)s::corpus.id_scheme
       AND r.local_id = %(law)s
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

#: 依條文版本標準 IRI 直接命中唯一索引，毫秒級取得單一條文全文。
_GET_ARTICLE_BY_IRI = """
    SELECT corpus.version_iri(v.id), r.jurisdiction::text, r.id_scheme::text, r.local_id,
           e.title, coalesce(corpus.article_number(p.path), p.path),
           coalesce(v.content, ''), e.language, e.version_date::text, r.norm_kind::text,
           r.document_type, r.repealed_on::text
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e
        ON e.resource_id = r.id
       AND e.language = %(language)s
       AND e.version_date = %(version_date)s::date
      JOIN corpus.provision AS p
        ON p.resource_id = r.id
       AND p.path = %(path)s
      JOIN corpus.provision_version AS v
        ON v.provision_id = p.id
       AND v.expression_id = e.id
       AND v.superseded_at IS NULL
     WHERE r.local_id = %(local_id)s
       AND corpus.version_iri(v.id) = %(iri)s
     LIMIT 1
"""

#: 該日有效的條，含 `text_unavailable`（有效，只是無文字）。
_LIST_ARTICLES = """
    SELECT corpus.version_iri(s.version_id), corpus.article_number(s.path), s.sort_key
      FROM corpus.provisions_at(%(resource)s, %(language)s, %(on)s::date) AS s
     WHERE s.provision_type = 'article'
       AND s.status IN ('in_force', 'text_unavailable')
     ORDER BY s.sort_key
"""

_LIST_HEADINGS = """
    SELECT h.id, h.parent_id, h.heading_kind::text, h.heading_no, h.title, h.sort_key
      FROM corpus.structure_heading AS h
      JOIN corpus.legal_expression AS e ON e.id = h.expression_id
     WHERE h.resource_id = %(resource)s
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
     ORDER BY h.sort_key
"""

_JURISDICTIONS = "SELECT unnest(enum_range(NULL::corpus.jurisdiction))::text"

_ID_SCHEMES = "SELECT unnest(enum_range(NULL::corpus.id_scheme))::text"

_LIST_LAWS = """
    WITH valid_res AS (
        SELECT r.id, r.jurisdiction, r.id_scheme, r.local_id,
               r.norm_kind, r.document_type, e.title
          FROM corpus.legal_resource AS r
          JOIN corpus.legal_expression AS e ON e.resource_id = r.id
         WHERE e.language = %(language)s
           AND e.text_period @> %(on)s::date
           AND e.text_status = 'available'
    ),
    active_del AS (
        SELECT d.law_resource_id, d.command_resource_id, min(d.id) AS first_id
          FROM annotation.delegation_usable AS d
          JOIN valid_res AS vl ON vl.id = d.law_resource_id
          JOIN valid_res AS vc ON vc.id = d.command_resource_id
         WHERE annotation.delegation_command_in_force(
                   d.command_resource_id,
                   d.command_provision_id,
                   d.command_content_sha256,
                   %(language)s,
                   %(on)s::date
               )
         GROUP BY d.law_resource_id, d.command_resource_id
    ),
    child_cnt AS (
        SELECT law_resource_id AS resource_id, count(*)::int AS cnt
          FROM active_del
         GROUP BY law_resource_id
    ),
    parent_cnt AS (
        SELECT command_resource_id AS resource_id, count(*)::int AS cnt
          FROM active_del
         GROUP BY command_resource_id
    ),
    primary_parent AS (
        SELECT DISTINCT ON (ad.command_resource_id)
               ad.command_resource_id, vl.jurisdiction, vl.id_scheme, vl.local_id
          FROM active_del AS ad
          JOIN valid_res AS vl ON vl.id = ad.law_resource_id
         ORDER BY ad.command_resource_id, ad.first_id
    ),
    guidance_cnt AS (
        SELECT p.resource_id, count(DISTINCT gd.id)::int AS cnt
          FROM guidance.interpretation AS gi
          JOIN guidance.document AS gd ON gd.id = gi.document_id
          JOIN corpus.provision AS p ON p.id = gi.provision_id
          JOIN corpus.provision_version AS pv
            ON pv.provision_id = gi.provision_id
           AND pv.content_sha256 = gi.content_sha256
         WHERE pv.language = %(language)s
           AND gd.issued_on <= %(on)s::date
           AND pv.superseded_at IS NULL
           AND pv.force_period @> %(on)s::date
           AND NOT EXISTS (
               SELECT 1
                 FROM guidance.cessation AS cs
                WHERE cs.document_id = gd.id
                  AND cs.ceased_on <= %(on)s::date
           )
         GROUP BY p.resource_id
    ),
    enactment AS (
        SELECT resource_id, min(event_date)::text AS date_document
          FROM corpus.lifecycle_event
         WHERE event_kind = 'enactment'
         GROUP BY resource_id
    )
    SELECT r.jurisdiction::text, r.id_scheme::text, r.local_id,
           corpus.resource_iri(r.norm_kind, r.local_id), r.title, r.norm_kind::text,
           coalesce(en.date_document, ''),
           pp.jurisdiction::text, pp.id_scheme::text, pp.local_id,
           r.document_type,
           coalesce(cc.cnt, 0),
           coalesce(pc.cnt, 0),
           coalesce(gc.cnt, 0)
      FROM valid_res AS r
      LEFT JOIN enactment AS en ON en.resource_id = r.id
      LEFT JOIN primary_parent AS pp ON pp.command_resource_id = r.id
      LEFT JOIN child_cnt AS cc ON cc.resource_id = r.id
      LEFT JOIN parent_cnt AS pc ON pc.resource_id = r.id
      LEFT JOIN guidance_cnt AS gc ON gc.resource_id = r.id
     WHERE (
             %(jurisdiction)s::text IS NULL
             OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction
           )
       AND (
             %(prefix)s::text IS NULL
             OR starts_with(r.local_id, %(prefix)s)
           )
     ORDER BY r.norm_kind, r.local_id
"""


class _ResolvedRow(NamedTuple):
    resource_id: int
    iri: str
    title: str
    language: str
    enacted: date | None
    jurisdiction: str
    id_scheme: str
    local_id: str


class _ArticleRow(NamedTuple):
    iri: str
    number: str
    sort_key: list[int]


class _HeadingRow(NamedTuple):
    id: int
    parent_id: int | None
    kind: str
    heading_no: str
    title: str
    sort_key: list[int]


class _InvalidationRow(NamedTuple):
    ruling_no: str
    ruling_kind: str
    decided_on: date
    source_url: str
    excerpt: str
    path: str
    invalid_from: date
    partial: bool


class _ValueRow(NamedTuple):
    value: str


_VERSION_IRI_RE = re.compile(
    r"^https://envcite/tw/(?:law|order)/([^/]+)/(\d{4}-\d{2}-\d{2})/([^/]+)/(.+)$"
)


class LawRepository:
    """法規與條文的唯讀查詢；連線由外面給（見 `ports.ConnectionSource`）。"""

    def __init__(self, connections: ConnectionSource) -> None:
        self._connections = connections

    def get_law(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> LawRecord | None:
        """`as_of`（ISO 日期，空為今日）有效版本的法規；法規不存在或該日無版本時為 None。"""
        found = self.resolve(key, as_of, timeout=timeout)
        return found[1] if found else None

    def get_article_by_iri(
        self, iri: str, *, timeout: float | None = None
    ) -> ArticleDetailRecord | None:
        """依條文版本標準 IRI 查詢單一條文全文與中繼資料；格式不符或不存在時回 None。"""
        m = _VERSION_IRI_RE.match(iri.strip())
        if m is None:
            return None
        local_id, version_date, language, path = m.groups()
        with self._connections.read_connection(timeout=timeout) as conn:
            return fetch_one(
                conn,
                _GET_ARTICLE_BY_IRI,
                {
                    "local_id": local_id,
                    "version_date": version_date,
                    "language": language,
                    "path": path,
                    "iri": iri.strip(),
                },
                ArticleDetailRecord,
            )

    def list_laws(
        self,
        *,
        as_of: str = "",
        jurisdiction: str | None = None,
        category_prefix: str | None = None,
        timeout: float | None = None,
    ) -> list[LawSummaryRecord]:
        """列出 `as_of` 當日有效且有全文的法規摘要與授權統計。"""
        with self._connections.read_connection(timeout=timeout) as conn:
            return fetch(
                conn,
                _LIST_LAWS,
                {
                    "language": LANGUAGE,
                    "on": as_of_param(as_of),
                    "jurisdiction": jurisdiction,
                    "prefix": category_prefix,
                },
                LawSummaryRecord,
            )

    def list_articles(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[ArticleRecord] | None:
        """`as_of` 當日有效的各條；法規或版本不存在為 None，有版本而無有效條為 []，兩者語意不同。"""
        deadline = expiry(timeout)
        found = self.resolve(key, as_of, timeout=time_left(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self._connections.read_connection(timeout=time_left(deadline)) as conn:
            rows = fetch(
                conn,
                _LIST_ARTICLES,
                {"resource": resource_id, "language": LANGUAGE, "on": as_of_param(as_of)},
                _ArticleRow,
            )
        return [ArticleRecord(iri=r.iri, number=r.number, sort_key=tuple(r.sort_key)) for r in rows]

    def list_headings(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[StructureHeadingRecord] | None:
        """`as_of` 當日有效版本的結構章節；法規或版本不存在為 None。"""
        deadline = expiry(timeout)
        found = self.resolve(key, as_of, timeout=time_left(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self._connections.read_connection(timeout=time_left(deadline)) as conn:
            rows = fetch(
                conn,
                _LIST_HEADINGS,
                {"resource": resource_id, "language": LANGUAGE, "on": as_of_param(as_of)},
                _HeadingRow,
            )
        id_to_index = {row.id: i for i, row in enumerate(rows)}
        return [
            StructureHeadingRecord(
                kind=row.kind,
                heading_no=row.heading_no,
                title=row.title,
                sort_key=tuple(row.sort_key),
                parent_index=id_to_index.get(row.parent_id, -1)
                if row.parent_id is not None
                else -1,
            )
            for row in rows
        ]

    def list_invalidations(
        self,
        key: LawKey,
        *,
        as_of: str = "",
        article_number: str = "",
        timeout: float | None = None,
    ) -> list[InvalidationRecord] | None:
        """`as_of` 當日因違憲而失效的條文單元；法規或版本不存在為 None，沒有失效為 []。

        `article_number` 非空時只看該條（含其下各項）。
        """
        deadline = expiry(timeout)
        found = self.resolve(key, as_of, timeout=time_left(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self._connections.read_connection(timeout=time_left(deadline)) as conn:
            rows = fetch(
                conn,
                "SELECT i.ruling_no, r.ruling_kind::text, r.decided_on, r.source_url, r.excerpt,"
                "       i.path, i.invalid_from, x.partial"
                "  FROM corpus.invalidations_at(%(resource)s, %(language)s, %(on)s::date) AS i"
                "  JOIN corpus.constitutional_ruling AS r ON r.id = i.ruling_id"
                "  JOIN corpus.ruling_invalidation AS x"
                "    ON x.ruling_id = i.ruling_id AND x.provision_id = i.provision_id"
                " WHERE %(article)s = '' OR i.path = 'art_' || %(article)s"
                "       OR starts_with(i.path, 'art_' || %(article)s || '/')"
                " ORDER BY r.decided_on, i.ruling_id, i.provision_id",
                {
                    "resource": resource_id,
                    "language": LANGUAGE,
                    "on": as_of_param(as_of),
                    "article": article_number.strip(),
                },
                _InvalidationRow,
            )
        return [
            InvalidationRecord(
                r.ruling_no,
                r.ruling_kind,
                r.decided_on.isoformat(),
                r.source_url,
                r.excerpt,
                r.path,
                r.invalid_from.isoformat(),
                r.partial,
            )
            for r in rows
        ]

    def law_exists(self, key: LawKey, *, timeout: float | None = None) -> bool:
        """法規在語料裡有沒有，不論哪一天。區分「查無此法」與「該日無版本」。"""
        if not is_valid_law_id(key.local_id):
            return False
        with self._connections.read_connection(timeout=timeout) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM corpus.legal_resource"
                    " WHERE jurisdiction = %s::corpus.jurisdiction"
                    "   AND id_scheme = %s::corpus.id_scheme"
                    "   AND local_id = %s",
                    (key.jurisdiction, key.id_scheme, key.local_id),
                ).fetchone()
                is not None
            )

    def resource_id(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> int | None:
        """`as_of` 有效版本的法規之內部編號；法規不存在或該日無版本時為 None。"""
        found = self.resolve(key, as_of, timeout=timeout)
        return found[0] if found else None

    @cached_property
    def jurisdictions(self) -> frozenset[str]:
        """綱要接受的管轄（`corpus.jurisdiction`）；封閉集合，啟動後第一次用到時讀一次。"""
        return self._values(_JURISDICTIONS)

    @cached_property
    def id_schemes(self) -> frozenset[str]:
        """綱要接受的識別碼方案（`corpus.id_scheme`）；讀取時機同 `jurisdictions`。"""
        return self._values(_ID_SCHEMES)

    def _values(self, query: str) -> frozenset[str]:
        with self._connections.connection() as conn:
            return frozenset(row.value for row in fetch(conn, query, None, _ValueRow))

    def resolve(
        self, key: LawKey, as_of: str, *, timeout: float | None = None
    ) -> tuple[int, LawRecord] | None:
        """`as_of` 有效、有全文版本的法規（內部編號與記錄）；法規不存在或該日無版本為 None。"""
        if not is_valid_law_id(key.local_id):
            return None
        with self._connections.read_connection(timeout=timeout) as conn:
            row = fetch_one(
                conn,
                _RESOLVE,
                {
                    "law": key.local_id,
                    "language": LANGUAGE,
                    "on": as_of_param(as_of),
                    "jurisdiction": key.jurisdiction,
                    "id_scheme": key.id_scheme,
                },
                _ResolvedRow,
            )
        if row is None:
            return None
        return row.resource_id, LawRecord(
            jurisdiction=row.jurisdiction,
            id_scheme=row.id_scheme,
            local_id=row.local_id,
            iri=row.iri,
            title=row.title,
            language=row.language,
            date_document=row.enacted.isoformat() if row.enacted is not None else "",
        )
