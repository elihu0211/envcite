"""法規語料（PostgreSQL）之上的唯讀模型；資料表是唯一真值。

版本解析、閱讀順序、IRI、條文全文都由 SQL 決定，本模組不重算，以免兩處答案分岔。
"""

import re
import time
from collections import Counter, defaultdict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from types import TracebackType
from typing import Any, Self

import psycopg
from psycopg_pool import ConnectionPool

from envcite_ontology.embedding import to_sql
from envcite_ontology.law_names import LawNames

#: 語料目前只有中文版本。
LANGUAGE = "zho"


@dataclass(frozen=True, slots=True)
class LawKey:
    """法規的自然鍵（管轄，識別碼方案，在地識別碼）；三個欄位都由呼叫端明說，沒有預設。

    管轄與方案是否配對由資料庫強制（`legal_resource_scheme_matches_jurisdiction`）。
    """

    jurisdiction: str
    id_scheme: str
    local_id: str

    def __str__(self) -> str:
        return f"{self.jurisdiction}:{self.id_scheme}:{self.local_id}"


@dataclass(frozen=True, slots=True)
class LawRecord:
    """某日有效的法規版本：自然鍵、IRI、標題、語言與制定日。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    language: str
    #: 制定日（ISO 日期）；語料沒有制定事件則為空字串。
    date_document: str


@dataclass(frozen=True, slots=True)
class LawSummaryRecord:
    """語料庫中某日有效的一部法規摘要，含位階與已解析之授權母法自然鍵。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    norm_kind: str
    date_document: str
    parent_jurisdiction: str | None = None
    parent_id_scheme: str | None = None
    parent_local_id: str | None = None
    document_type: str = ""
    child_count: int = 0
    parent_count: int = 0
    guidance_count: int = 0


@dataclass(frozen=True, slots=True)
class LawGraphNodeRecord:
    """授權知識圖譜中的一個法規節點（含凱爾森位階層級與統計摘要）。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    norm_kind: str
    document_type: str
    date_document: str
    hierarchy_tier: int
    total_children: int
    truncated_children: int
    article_count: int
    invalidation_count: int
    guidance_count: int


@dataclass(frozen=True, slots=True)
class LawGraphEdgeRecord:
    """授權知識圖譜中的一條有向授權邊（法律→命令，或命令→再授權子規則）。"""

    source_jurisdiction: str
    source_id_scheme: str
    source_local_id: str
    target_jurisdiction: str
    target_id_scheme: str
    target_local_id: str
    kind: str
    anchor_articles: tuple[str, ...]
    all_rooted: bool


@dataclass(frozen=True, slots=True)
class ArticleViolationLinkRecord:
    """法規內部罰則與義務／授權條文之連動關係。"""

    penalty_article: str
    penalty_path: str
    kind: str
    target_article: str
    target_path: str


@dataclass(frozen=True, slots=True)
class GuidanceLinkRecord:
    """解釋法規條文且於基準日仍有效之行政函釋摘要（行政程序法第 159 條）。"""

    article_number: str
    path: str
    document_no: str
    issuer: str
    issued_on: str
    subject: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ChildGroupCountRecord:
    """直接下位授權法規依《中央法規標準法》定名種類統計之數量。"""

    document_type: str
    count: int


@dataclass(frozen=True, slots=True)
class LawGraphRecord:
    """`GetLawGraph` 查詢結果（焦點子圖或核心母法總覽）。"""

    focus_jurisdiction: str | None
    focus_id_scheme: str | None
    focus_local_id: str | None
    nodes: tuple[LawGraphNodeRecord, ...]
    edges: tuple[LawGraphEdgeRecord, ...]
    violation_links: tuple[ArticleViolationLinkRecord, ...]
    guidance_links: tuple[GuidanceLinkRecord, ...]
    child_groups: tuple[ChildGroupCountRecord, ...]


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    """某日有效的一條：版本 IRI、條號與排序鍵。"""

    iri: str
    number: str
    sort_key: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureHeadingRecord:
    """某版本的一筆結構章節（編、章、節、款、目）。"""

    kind: str
    heading_no: str
    title: str
    sort_key: tuple[int, ...]
    parent_index: int


@dataclass(frozen=True, slots=True)
class InvalidationRecord:
    """某日因違憲失效的一個條文單元，連同使它失效的裁判。"""

    ruling_no: str
    #: `corpus.ruling_kind` 的值：interpretation 或 judgment
    ruling_kind: str
    decided_on: str
    source_url: str
    excerpt: str
    path: str
    invalid_from: str
    #: 裁判只宣告條文的一部分；失效仍記在整個條文單元上。
    partial: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一筆檢索命中的條文及其分數。"""

    iri: str
    jurisdiction: str
    id_scheme: str
    local_id: str
    law_title: str
    number: str
    content: str
    lexical_score: float
    #: 語意臂的餘弦相似度；語意臂沒有命中這一條、或沒有啟用語意臂時為 None。
    semantic_similarity: float | None = None


def is_valid_law_id(value: str) -> bool:
    """法規識別碼是否可查：非空且不超過 64 字元。"""
    return bool(value and len(value) <= 64)


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

_GRAPH_RESOURCES = """
    SELECT r.id, r.norm_kind::text, r.document_type, r.local_id
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
     WHERE e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

_GRAPH_ACTIVE_EDGES = """
    SELECT DISTINCT d.law_resource_id, d.command_resource_id
      FROM annotation.delegation_usable AS d
      JOIN corpus.legal_expression AS el ON el.resource_id = d.law_resource_id
      JOIN corpus.legal_expression AS ec ON ec.resource_id = d.command_resource_id
     WHERE d.law_resource_id IS NOT NULL
       AND el.language = %(language)s
       AND el.text_period @> %(on)s::date
       AND el.text_status = 'available'
       AND ec.language = %(language)s
       AND ec.text_period @> %(on)s::date
       AND ec.text_status = 'available'
       AND annotation.delegation_command_in_force(
               d.command_resource_id,
               d.command_provision_id,
               d.command_content_sha256,
               %(language)s,
               %(on)s::date
           )
"""

_GRAPH_NODE_DETAILS = """
    WITH req AS (
        SELECT unnest(%(ids)s::bigint[]) AS id
    ),
    enactment AS (
        SELECT resource_id, min(event_date)::text AS date_document
          FROM corpus.lifecycle_event
         WHERE event_kind = 'enactment'
           AND resource_id = ANY(%(ids)s::bigint[])
         GROUP BY resource_id
    ),
    art_cnt AS (
        SELECT req.id AS resource_id, count(*)::int AS cnt
          FROM req
         CROSS JOIN LATERAL corpus.provisions_at(req.id, %(language)s, %(on)s::date) AS p
         WHERE p.provision_type = 'article'
           AND p.status IN ('in_force', 'text_unavailable')
         GROUP BY req.id
    ),
    inv_cnt AS (
        SELECT req.id AS resource_id, count(DISTINCT i.provision_id)::int AS cnt
          FROM req
         CROSS JOIN LATERAL corpus.invalidations_at(req.id, %(language)s, %(on)s::date) AS i
         GROUP BY req.id
    ),
    guide_cnt AS (
        SELECT p.resource_id, count(DISTINCT gd.id)::int AS cnt
          FROM guidance.interpretation AS gi
          JOIN guidance.document AS gd ON gd.id = gi.document_id
          JOIN corpus.provision AS p ON p.id = gi.provision_id
          JOIN corpus.provision_version AS pv
            ON pv.provision_id = gi.provision_id
           AND pv.content_sha256 = gi.content_sha256
         WHERE p.resource_id = ANY(%(ids)s::bigint[])
           AND pv.language = %(language)s
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
    )
    SELECT r.id, r.jurisdiction::text, r.id_scheme::text, r.local_id,
           corpus.resource_iri(r.norm_kind, r.local_id), e.title,
           r.norm_kind::text, r.document_type, coalesce(en.date_document, ''),
           coalesce(ac.cnt, 0), coalesce(ic.cnt, 0), coalesce(gc.cnt, 0)
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
      LEFT JOIN enactment AS en ON en.resource_id = r.id
      LEFT JOIN art_cnt AS ac ON ac.resource_id = r.id
      LEFT JOIN inv_cnt AS ic ON ic.resource_id = r.id
      LEFT JOIN guide_cnt AS gc ON gc.resource_id = r.id
     WHERE r.id = ANY(%(ids)s::bigint[])
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

_GRAPH_EDGE_DETAILS = """
    SELECT d.law_resource_id, d.command_resource_id,
           array_agg(DISTINCT d.cited_path ORDER BY d.cited_path)
               FILTER (WHERE d.cited_path <> '') AS anchors,
           bool_and(
               d.law_provision_id IS NOT NULL
               AND a.status = 'in_force'
               AND NOT EXISTS (
                   SELECT 1
                     FROM corpus.invalidations_at(
                              d.law_resource_id, %(language)s, %(on)s::date
                          ) AS inv
                    WHERE inv.version_id = a.version_id
               )
           ) AS all_rooted
      FROM annotation.delegation_usable AS d
      LEFT JOIN LATERAL corpus.provisions_at(d.law_resource_id, %(language)s, %(on)s::date) AS a
           ON a.provision_id = d.law_provision_id
     WHERE d.law_resource_id = ANY(%(ids)s::bigint[])
       AND d.command_resource_id = ANY(%(ids)s::bigint[])
       AND annotation.delegation_command_in_force(
               d.command_resource_id,
               d.command_provision_id,
               d.command_content_sha256,
               %(language)s,
               %(on)s::date
           )
     GROUP BY d.law_resource_id, d.command_resource_id
     ORDER BY d.law_resource_id, d.command_resource_id
"""

_GRAPH_VIOLATION_LINKS = """
    SELECT DISTINCT ON (p.sort_key, p.path, vi.violation_kind, vi.cited_path)
           coalesce(corpus.article_number(p.path), p.path),
           p.path,
           vi.violation_kind::text,
           coalesce(corpus.article_number(vi.cited_path), vi.cited_path),
           vi.cited_path
      FROM annotation.current_violation AS vi
      JOIN corpus.provision AS p ON p.id = vi.provision_id
      JOIN corpus.provision_version AS pv
        ON pv.provision_id = vi.provision_id
       AND pv.content_sha256 = vi.content_sha256
     WHERE p.resource_id = %(resource)s
       AND pv.language = %(language)s
       AND pv.superseded_at IS NULL
       AND pv.force_period @> %(on)s::date
     ORDER BY p.sort_key, p.path, vi.violation_kind, vi.cited_path
"""

_GRAPH_GUIDANCE_LINKS = """
    SELECT article_number, path, document_no, issuer, issued_on, subject, source_url
      FROM (
          SELECT DISTINCT ON (gd.id, coalesce(corpus.article_number(p.path), p.path))
                 coalesce(corpus.article_number(p.path), p.path) AS article_number,
                 p.path,
                 gd.document_no,
                 gd.issuer,
                 gd.issued_on::text AS issued_on,
                 left(btrim(split_part(gd.subject, '說明：', 1)), 160) AS subject,
                 coalesce(gd.source_url, '') AS source_url,
                 gd.issued_on AS issued_on_date,
                 p.sort_key AS sort_key
            FROM guidance.interpretation AS gi
            JOIN guidance.document AS gd ON gd.id = gi.document_id
            JOIN corpus.provision AS p ON p.id = gi.provision_id
            JOIN corpus.provision_version AS pv
              ON pv.provision_id = gi.provision_id
             AND pv.content_sha256 = gi.content_sha256
           WHERE p.resource_id = %(resource)s
             AND pv.language = %(language)s
             AND gd.issued_on <= %(on)s::date
             AND pv.superseded_at IS NULL
             AND pv.force_period @> %(on)s::date
             AND NOT EXISTS (
                 SELECT 1
                   FROM guidance.cessation AS cs
                  WHERE cs.document_id = gd.id
                    AND cs.ceased_on <= %(on)s::date
             )
           ORDER BY gd.id, coalesce(corpus.article_number(p.path), p.path), p.sort_key
      ) AS sub
     ORDER BY issued_on_date DESC, sort_key, document_no
     LIMIT 30
"""

#: segmenter 詞典（法規名稱）；須與建索引同源，否則分詞器識別碼不符、字面檢索全落空（04-01 §5）。
_LEXICON = "SELECT DISTINCT title FROM corpus.legal_expression WHERE title IS NOT NULL"

#: 標題（含各版本的舊名稱）→ 法規代碼；問句點名法規用（`law_names.LawNames`）。
_LAW_TITLES = """
    SELECT e.title, array_agg(DISTINCT r.local_id)
      FROM corpus.legal_expression AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     WHERE e.title IS NOT NULL
     GROUP BY e.title
"""

_SEARCH = """
    SELECT corpus.version_iri(s.version_id), r.jurisdiction, r.id_scheme, r.local_id, e.title,
           corpus.article_number(s.path), corpus.article_text(s.version_id),
           s.lexical_score, s.semantic_similarity
      FROM retrieval.search_expanded(
               retrieval.prune_terms(%(terms)s, %(tokenizer)s, %(max_df)s::double precision),
               %(tokenizer)s, %(embedding)s::vector, %(model)s,
               %(on)s::date, %(language)s, %(limit)s, %(pool)s,
               CASE WHEN cardinality(%(laws)s::text[]) = 0 THEN NULL
                    ELSE retrieval.law_scope(%(laws)s::text[], %(language)s, %(on)s::date)
               END
           ) AS s
      JOIN corpus.legal_resource AS r ON r.id = s.resource_id
      JOIN corpus.provision_version AS v ON v.id = s.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE (%(jurisdiction)s::corpus.jurisdiction IS NULL
            OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction)
     ORDER BY s.rank
"""

_GUIDANCE = """
    SELECT corpus.version_iri(g.version_id), r.jurisdiction, r.id_scheme, r.local_id, e.title,
           corpus.article_number(g.path), corpus.article_text(g.version_id), g.documents
      FROM retrieval.guidance_articles(
               %(phrases)s::text[], %(on)s::date, %(language)s, %(limit)s,
               %(max_df)s::double precision) AS g
      JOIN corpus.legal_resource AS r ON r.id = g.resource_id
      JOIN corpus.provision_version AS v ON v.id = g.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE (%(jurisdiction)s::corpus.jurisdiction IS NULL
            OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction)
     ORDER BY g.documents DESC, g.version_id
"""

#: 每一臂的候選池（條數）。融合前各取這麼多；尚未以題庫量測，取 limit 上限的數倍。
SEARCH_POOL = 50


class OntologyStore:
    """法規語料（PostgreSQL）的唯讀查詢入口，持有連線池；可用 `with` 在結束時關閉。"""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> Self:
        """以 DSN 建立並開啟連線池，回傳 store。"""
        return cls(ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True))

    def close(self) -> None:
        """關閉連線池。"""
        self._pool.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get_law(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> LawRecord | None:
        """`as_of`（ISO 日期，空為今日）有效版本的法規；法規不存在或該日無版本時為 None。"""
        found = self._resolve(key, as_of, timeout=timeout)
        return found[1] if found else None

    def list_laws(
        self,
        *,
        as_of: str = "",
        jurisdiction: str | None = None,
        category_prefix: str | None = None,
        timeout: float | None = None,
    ) -> list[LawSummaryRecord]:
        """列出 `as_of` 當日有效且有全文的法規摘要與授權統計。"""
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _LIST_LAWS,
                {
                    "language": LANGUAGE,
                    "on": _date(as_of),
                    "jurisdiction": jurisdiction,
                    "prefix": category_prefix,
                },
            )
            return [
                LawSummaryRecord(
                    jurisdiction=row[0],
                    id_scheme=row[1],
                    local_id=row[2],
                    iri=row[3],
                    title=row[4],
                    norm_kind=row[5],
                    date_document=row[6],
                    parent_jurisdiction=row[7],
                    parent_id_scheme=row[8],
                    parent_local_id=row[9],
                    document_type=row[10],
                    child_count=row[11],
                    parent_count=row[12],
                    guidance_count=row[13],
                )
                for row in cur.fetchall()
            ]

    def get_law_graph(
        self,
        key: LawKey | None,
        *,
        as_of: str = "",
        category_prefix: str | None = None,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
        child_limit: int = 20,
        child_document_type: str = "",
        timeout: float | None = None,
    ) -> LawGraphRecord | None:
        """取得多層授權知識圖譜（焦點子圖或總覽）；指定法規無版本時為 None。"""
        deadline = _deadline(timeout)
        focus_id: int | None = None
        if key is not None:
            found = self._resolve(key, as_of, timeout=_remaining(deadline))
            if found is None:
                return None
            focus_id = found[0]

        on = _date(as_of)
        res_info, parents_of, children_of = self._load_graph_topology(
            on, timeout=_remaining(deadline)
        )
        tiers = _compute_tiers(res_info, children_of, parents_of)
        selected = _select_subgraph_nodes(
            focus_id=focus_id,
            res_info=res_info,
            parents_of=parents_of,
            children_of=children_of,
            category_prefix=category_prefix,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
            child_limit=child_limit,
            child_document_type=child_document_type,
        )
        return self._assemble_graph(
            focus_id=focus_id,
            selected=selected,
            res_info=res_info,
            children_of=children_of,
            tiers=tiers,
            on=on,
            deadline=deadline,
        )

    def _load_graph_topology(
        self, on: str, *, timeout: float | None
    ) -> tuple[dict[int, tuple[str, str, str]], dict[int, list[int]], dict[int, list[int]]]:
        params = {"language": LANGUAGE, "on": on}
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(_GRAPH_RESOURCES, params)
            res_info: dict[int, tuple[str, str, str]] = {
                int(rid): (str(nk), str(dt), str(lid)) for rid, nk, dt, lid in cur.fetchall()
            }
            cur.execute(_GRAPH_ACTIVE_EDGES, params)
            parents_of: dict[int, list[int]] = defaultdict(list)
            children_of: dict[int, list[int]] = defaultdict(list)
            for law_id, cmd_id in cur.fetchall():
                src, dst = int(law_id), int(cmd_id)
                children_of[src].append(dst)
                parents_of[dst].append(src)
        return res_info, parents_of, children_of

    def _assemble_graph(
        self,
        *,
        focus_id: int | None,
        selected: set[int],
        res_info: dict[int, tuple[str, str, str]],
        children_of: dict[int, list[int]],
        tiers: dict[int, int],
        on: str,
        deadline: float | None,
    ) -> LawGraphRecord:
        ids = sorted(selected)
        node_map: dict[int, LawGraphNodeRecord] = {}
        edges: list[LawGraphEdgeRecord] = []
        violations: list[ArticleViolationLinkRecord] = []
        guidances: list[GuidanceLinkRecord] = []
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            if ids:
                cur.execute(_GRAPH_NODE_DETAILS, {"ids": ids, "language": LANGUAGE, "on": on})
                node_map = _build_node_map(cur.fetchall(), children_of, selected, tiers)
                cur.execute(_GRAPH_EDGE_DETAILS, {"ids": ids, "language": LANGUAGE, "on": on})
                edges = _build_edges(cur.fetchall(), node_map)
            if focus_id is not None:
                params = {"resource": focus_id, "language": LANGUAGE, "on": on}
                cur.execute(_GRAPH_VIOLATION_LINKS, params)
                violations = [
                    ArticleViolationLinkRecord(
                        penalty_article=r[0],
                        penalty_path=r[1],
                        kind=r[2],
                        target_article=r[3],
                        target_path=r[4],
                    )
                    for r in cur.fetchall()
                ]
                cur.execute(_GRAPH_GUIDANCE_LINKS, params)
                guidances = [
                    GuidanceLinkRecord(
                        article_number=r[0],
                        path=r[1],
                        document_no=r[2],
                        issuer=r[3],
                        issued_on=r[4],
                        subject=r[5],
                        source_url=r[6],
                    )
                    for r in cur.fetchall()
                ]

        focus_node = node_map.get(focus_id) if focus_id is not None else None
        child_groups = _child_groups(focus_id, children_of, res_info) if focus_id else ()
        nodes = tuple(
            sorted(
                node_map.values(), key=lambda n: (n.hierarchy_tier, -n.total_children, n.local_id)
            )
        )
        return LawGraphRecord(
            focus_jurisdiction=focus_node.jurisdiction if focus_node else None,
            focus_id_scheme=focus_node.id_scheme if focus_node else None,
            focus_local_id=focus_node.local_id if focus_node else None,
            nodes=nodes,
            edges=tuple(edges),
            violation_links=tuple(violations),
            guidance_links=tuple(guidances),
            child_groups=child_groups,
        )

    def list_articles(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[ArticleRecord] | None:
        """`as_of` 當日有效的各條；法規或版本不存在為 None，有版本而無有效條為 []，兩者語意不同。"""
        deadline = _deadline(timeout)
        found = self._resolve(key, as_of, timeout=_remaining(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            cur.execute(
                _LIST_ARTICLES,
                {"resource": resource_id, "language": LANGUAGE, "on": _date(as_of)},
            )
            return [
                ArticleRecord(iri=iri, number=number, sort_key=tuple(sort_key))
                for iri, number, sort_key in cur.fetchall()
            ]

    def list_headings(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[StructureHeadingRecord] | None:
        """`as_of` 當日有效版本的結構章節；法規或版本不存在為 None。"""
        deadline = _deadline(timeout)
        found = self._resolve(key, as_of, timeout=_remaining(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            cur.execute(
                _LIST_HEADINGS,
                {"resource": resource_id, "language": LANGUAGE, "on": _date(as_of)},
            )
            rows = cur.fetchall()
        id_to_index = {row[0]: i for i, row in enumerate(rows)}
        return [
            StructureHeadingRecord(
                kind=row[2],
                heading_no=row[3],
                title=row[4],
                sort_key=tuple(row[5]),
                parent_index=id_to_index.get(row[1], -1) if row[1] is not None else -1,
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
        deadline = _deadline(timeout)
        found = self._resolve(key, as_of, timeout=_remaining(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            cur.execute(
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
                    "on": _date(as_of),
                    "article": article_number.strip(),
                },
            )
            return [
                InvalidationRecord(
                    no, kind, decided.isoformat(), url, excerpt, path, since.isoformat(), partial
                )
                for no, kind, decided, url, excerpt, path, since, partial in cur.fetchall()
            ]

    def law_exists(self, key: LawKey, *, timeout: float | None = None) -> bool:
        """法規在語料裡有沒有，不論哪一天。區分「查無此法」與「該日無版本」。"""
        if not is_valid_law_id(key.local_id):
            return False
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM corpus.legal_resource"
                " WHERE jurisdiction = %s::corpus.jurisdiction"
                "   AND id_scheme = %s::corpus.id_scheme"
                "   AND local_id = %s",
                (key.jurisdiction, key.id_scheme, key.local_id),
            )
            return cur.fetchone() is not None

    def resource_id(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> int | None:
        """`as_of` 有效版本的法規之內部編號；法規不存在或該日無版本時為 None。"""
        found = self._resolve(key, as_of, timeout=timeout)
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
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query)
            return frozenset(str(value) for (value,) in cur.fetchall())

    @contextmanager
    def read_connection(
        self, *, timeout: float | None = None
    ) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        """唯讀連線，在一個交易內（結束時一律回滾）。

        `timeout`（秒）同時是跟池子等待的上限與這個交易的 `statement_timeout`：呼叫的期限用完，
        查詢被取消，也不在池裡等到呼叫端已經離開。沒有期限時沿用池子自己的等待上限，不設
        `statement_timeout`。

        期限已經用完時直接丟 `QueryCanceled`，不向池子要連線：等待時間不大於 0 時，psycopg
        即使池裡有空連線也丟 `PoolTimeout`，那會被誤記成容量用盡。列保持未定型，查詢在讀到的
        地方直接組成紀錄；收成 `object` 會讓每一欄都要再轉一次。
        """
        if timeout is not None and timeout <= 0:
            raise psycopg.errors.QueryCanceled("deadline already spent")
        with self._pool.connection(timeout=timeout) as conn, conn.transaction(force_rollback=True):
            if timeout is not None:
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(max(1, int(timeout * 1000))),),
                )
            yield conn

    def lexicon(self) -> list[str]:
        """segmenter 的詞典：語料所有版本的法規標題（不重複）。"""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LEXICON)
            return [title for (title,) in cur.fetchall()]

    def law_names(self) -> LawNames:
        """法規標題（含各版本的舊名稱）對法規代碼的對照，供問句點名法規用。"""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LAW_TITLES)
            return LawNames({title: pcodes for title, pcodes in cur.fetchall()})

    def guidance_hits(
        self,
        phrases: Sequence[str],
        *,
        as_of: str = "",
        limit: int = 10,
        jurisdiction: str | None = None,
        max_df: float = 0.0,
        timeout: float | None = None,
    ) -> list[tuple[SearchHit, int]]:
        """函釋（主旨或內文含每個詞組、`as_of` 有效）所解釋的條文，連同提到它的函釋件數；多的在前。

        函釋只用來找條文，不是依據（`retrieval.guidance_articles`）。命中沒有字面分數與語意相似度。
        命中的函釋占可用函釋的比例超過 `max_df` 時不回任何條：詞組出現在那麼多函釋裡，指不出哪幾條
        （0 為不限制）。
        """
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _GUIDANCE,
                {
                    "phrases": list(phrases),
                    "on": _date(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                    "jurisdiction": jurisdiction,
                    "max_df": max_df,
                },
            )
            return [
                (
                    SearchHit(
                        iri=iri,
                        jurisdiction=jur,
                        id_scheme=scheme,
                        local_id=lid,
                        law_title=title,
                        number=number,
                        content=content,
                        lexical_score=0.0,
                        semantic_similarity=None,
                    ),
                    int(documents),
                )
                for iri, jur, scheme, lid, title, number, content, documents in cur.fetchall()
            ]

    def searched_terms(
        self, terms: str, *, tokenizer: str, max_df: float = 0.0, timeout: float | None = None
    ) -> list[str]:
        """字面臂實際查的詞：`search` 以同樣的 `max_df` 略過高頻詞之後留下的詞。"""
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT retrieval.prune_terms("
                "%(terms)s, %(tokenizer)s, %(max_df)s::double precision)",
                {"terms": terms, "tokenizer": tokenizer, "max_df": max_df},
            )
            row = cur.fetchone()
        return str(row[0] if row else "").split()

    def search(
        self,
        terms: str,
        *,
        tokenizer: str,
        as_of: str = "",
        limit: int = 10,
        embedding: Sequence[float] | None = None,
        model: str = "",
        max_df: float = 0.0,
        laws: Sequence[str] = (),
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        """`as_of`（空為今日）有效的條，字面臂比對已分詞的 `terms`，語意臂比對 `embedding`。

        沒有 `embedding` 時只用字面臂。兩臂以名次倒數融合排序（`retrieval.search_hybrid`），每條之後
        緊接處罰它（或授權其所在命令的條）的罰則條，分數取帶出它的那一條（`retrieval.search_expanded`）。
        `max_df`（段落比例）大於 0 時，字面臂先略過出現在超過這個比例的段落的詞
        （`retrieval.prune_terms`）；0 為不修剪。

        `laws`（法規代碼）非空時兩臂只在這些法規與依其授權訂定的命令裡找
        （`retrieval.law_scope`）；範圍在 `as_of` 沒有任何可檢索的條時，改在全庫找，不回空。

        `timeout`（秒）是這一次檢索剩下的預算，兩次查詢共用；沒有期限時為 None。
        """
        deadline = _deadline(timeout)
        hits = self._search(
            terms,
            tokenizer,
            as_of,
            limit,
            embedding,
            model,
            max_df,
            tuple(laws),
            jurisdiction,
            timeout=_remaining(deadline),
        )
        if laws and not hits:
            hits = self._search(
                terms,
                tokenizer,
                as_of,
                limit,
                embedding,
                model,
                max_df,
                (),
                jurisdiction,
                timeout=_remaining(deadline),
            )
        return hits

    def _search(
        self,
        terms: str,
        tokenizer: str,
        as_of: str,
        limit: int,
        embedding: Sequence[float] | None,
        model: str,
        max_df: float,
        laws: tuple[str, ...],
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _SEARCH,
                {
                    "laws": list(laws),
                    "terms": terms,
                    "tokenizer": tokenizer,
                    "max_df": max_df,
                    "on": _date(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                    "embedding": None if embedding is None else to_sql(embedding),
                    "model": model,
                    "pool": SEARCH_POOL,
                    "jurisdiction": jurisdiction,
                },
            )
            return [
                SearchHit(
                    iri=iri,
                    jurisdiction=jur,
                    id_scheme=scheme,
                    local_id=lid,
                    law_title=title,
                    number=number,
                    content=content,
                    lexical_score=lexical,
                    semantic_similarity=semantic,
                )
                for (
                    iri,
                    jur,
                    scheme,
                    lid,
                    title,
                    number,
                    content,
                    lexical,
                    semantic,
                ) in cur.fetchall()
            ]

    def _resolve(
        self, key: LawKey, as_of: str, *, timeout: float | None = None
    ) -> tuple[int, LawRecord] | None:
        if not is_valid_law_id(key.local_id):
            return None
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _RESOLVE,
                {
                    "law": key.local_id,
                    "language": LANGUAGE,
                    "on": _date(as_of),
                    "jurisdiction": key.jurisdiction,
                    "id_scheme": key.id_scheme,
                },
            )
            row = cur.fetchone()
        if row is None:
            return None
        (
            resource_id,
            iri,
            title,
            language,
            enacted,
            jur,
            scheme,
            lid,
        ) = row
        return resource_id, LawRecord(
            jurisdiction=jur,
            id_scheme=scheme,
            local_id=lid,
            iri=iri,
            title=title,
            language=language,
            date_document=enacted.isoformat() if enacted is not None else "",
        )


def _date(as_of: str) -> str:
    """空 `as_of` 為今日，由資料庫解析以與查詢同一時鐘。"""
    return as_of or "today"


def _deadline(timeout: float | None) -> float | None:
    """`timeout` 秒後的 monotonic 時刻；沒有期限為 None。"""
    if timeout is None:
        return None
    return time.monotonic() + timeout


def _remaining(deadline: float | None) -> float | None:
    """距離 `deadline` 還剩的秒數；沒有期限為 None。"""
    if deadline is None:
        return None
    return deadline - time.monotonic()


_CITED_PATH_RE = re.compile(r"^art_(\d+)(?:-(\d+))?(?:/para_(\d+))?(?:/pt_(\d+))?$")


def _format_cited_path(path: str) -> str:
    """把標準化路徑（如 `art_42/para_2`）轉成人可讀之法條引述字串。"""
    m = _CITED_PATH_RE.match(path)
    if m is None:
        return path
    art, sub, para, pt = m.groups()
    out = f"第 {art} 條"
    if sub:
        out += f"之 {sub}"
    if para:
        out += f" 第 {para} 項" if sub else f"第 {para} 項"
    if pt:
        out += f"第 {pt} 款"
    return out


def _compute_tiers(
    res_info: dict[int, tuple[str, str, str]],
    children_of: dict[int, list[int]],
    parents_of: dict[int, list[int]],
) -> dict[int, int]:
    """依凱爾森位階從憲法(0)→法律(1)→第一層命令(2)→再授權子規則(3)計層。"""
    tiers: dict[int, int] = {}
    queue: deque[int] = deque()
    for target_kind, tier in (("constitution", 0), ("statute", 1)):
        for rid, (nk, _, _) in res_info.items():
            if nk == target_kind:
                tiers[rid] = tier
                queue.append(rid)

    while queue:
        src = queue.popleft()
        next_tier = 2 if tiers[src] <= 1 else 3
        for dst in children_of.get(src, ()):
            if dst not in res_info or res_info[dst][0] != "regulation":
                continue
            if dst not in tiers:
                tiers[dst] = next_tier
                queue.append(dst)

    for rid, (nk, _, _) in res_info.items():
        if rid not in tiers:
            tiers[rid] = 3 if (nk == "regulation" and parents_of.get(rid)) else 2
    return tiers


def _select_subgraph_nodes(
    *,
    focus_id: int | None,
    res_info: dict[int, tuple[str, str, str]],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    category_prefix: str | None,
    upstream_depth: int,
    downstream_depth: int,
    child_limit: int,
    child_document_type: str,
) -> set[int]:
    if focus_id is None:
        return _select_overview_nodes(
            res_info=res_info,
            parents_of=parents_of,
            children_of=children_of,
            category_prefix=category_prefix,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
            child_limit=child_limit,
            child_document_type=child_document_type,
        )
    included: set[int] = {focus_id}
    _walk_upstream(focus_id, parents_of, upstream_depth, included)
    _walk_downstream(
        focus_id,
        children_of,
        res_info,
        downstream_depth,
        child_limit,
        child_document_type,
        included,
    )
    return included


def _walk_upstream(
    focus_id: int,
    parents_of: dict[int, list[int]],
    depth: int,
    included: set[int],
) -> None:
    frontier = [focus_id]
    seen = {focus_id}
    for _ in range(depth):
        nxt: list[int] = []
        for nid in frontier:
            for pid in parents_of.get(nid, ()):
                included.add(pid)
                if pid not in seen:
                    seen.add(pid)
                    nxt.append(pid)
        frontier = nxt
        if not frontier:
            break


def _walk_downstream(
    focus_id: int,
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    depth: int,
    child_limit: int,
    child_document_type: str,
    included: set[int],
) -> None:
    frontier = [focus_id]
    seen = {focus_id}
    for hop in range(depth):
        nxt: list[int] = []
        type_filter = child_document_type if hop == 0 else ""
        for nid in frontier:
            candidates = _rank_children(
                children_of.get(nid, ()), children_of, res_info, type_filter
            )
            for cid in candidates[:child_limit]:
                included.add(cid)
                if cid not in seen:
                    seen.add(cid)
                    nxt.append(cid)
        frontier = nxt
        if not frontier:
            break


def _select_overview_nodes(
    *,
    res_info: dict[int, tuple[str, str, str]],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    category_prefix: str | None,
    upstream_depth: int,
    downstream_depth: int,
    child_limit: int,
    child_document_type: str,
) -> set[int]:
    included: set[int] = set()
    if category_prefix is None:
        constitutions = sorted(
            (rid for rid, (nk, _, _) in res_info.items() if nk == "constitution"),
            key=lambda rid: res_info[rid][2],
        )
        included.update(constitutions[:4])

    matching = [
        rid
        for rid, (_, _, lid) in res_info.items()
        if category_prefix is None or lid.startswith(category_prefix)
    ]
    matching.sort(
        key=lambda rid: (
            0
            if res_info[rid][0] == "statute" and children_of.get(rid)
            else (1 if children_of.get(rid) else (2 if res_info[rid][0] == "statute" else 3)),
            -len(children_of.get(rid, ())),
            res_info[rid][2],
        )
    )
    hubs = matching[:14]
    included.update(hubs)
    per_hub = max(1, min(3, child_limit))
    _expand_overview_branches(
        hubs=hubs,
        parents_of=parents_of,
        children_of=children_of,
        res_info=res_info,
        upstream_depth=upstream_depth,
        downstream_depth=downstream_depth,
        per_hub=per_hub,
        child_document_type=child_document_type,
        included=included,
    )
    return included


def _expand_overview_branches(
    *,
    hubs: list[int],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    upstream_depth: int,
    downstream_depth: int,
    per_hub: int,
    child_document_type: str,
    included: set[int],
) -> None:
    if upstream_depth >= 1:
        for hid in hubs:
            for pid in parents_of.get(hid, ()):
                if len(included) < 60:
                    included.add(pid)
    for hid in hubs:
        children = _rank_children(
            children_of.get(hid, ()), children_of, res_info, child_document_type
        )
        for cid in children[:per_hub]:
            if len(included) >= 56:
                return
            included.add(cid)
            if downstream_depth >= 2 and children_of.get(cid):
                sub = _rank_children(children_of[cid], children_of, res_info, "")
                if sub and len(included) < 60:
                    included.add(sub[0])


def _rank_children(
    raw: Sequence[int],
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    document_type: str,
) -> list[int]:
    filtered = [
        cid
        for cid in raw
        if cid in res_info and (not document_type or res_info[cid][1] == document_type)
    ]
    filtered.sort(key=lambda cid: (-len(children_of.get(cid, ())), res_info[cid][2]))
    return filtered


def _build_node_map(
    rows: list[tuple[Any, ...]],
    children_of: dict[int, list[int]],
    selected: set[int],
    tiers: dict[int, int],
) -> dict[int, LawGraphNodeRecord]:
    out: dict[int, LawGraphNodeRecord] = {}
    for row in rows:
        rid = int(row[0])
        direct = children_of.get(rid, ())
        shown = sum(1 for cid in direct if cid in selected)
        total = len(direct)
        out[rid] = LawGraphNodeRecord(
            jurisdiction=str(row[1]),
            id_scheme=str(row[2]),
            local_id=str(row[3]),
            iri=str(row[4]),
            title=str(row[5]),
            norm_kind=str(row[6]),
            document_type=str(row[7]),
            date_document=str(row[8]),
            hierarchy_tier=tiers.get(rid, 2),
            total_children=total,
            truncated_children=max(0, total - shown),
            article_count=int(row[9]),
            invalidation_count=int(row[10]),
            guidance_count=int(row[11]),
        )
    return out


def _build_edges(
    rows: list[tuple[Any, ...]], node_map: dict[int, LawGraphNodeRecord]
) -> list[LawGraphEdgeRecord]:
    edges: list[LawGraphEdgeRecord] = []
    for row in rows:
        src = node_map.get(int(row[0]))
        dst = node_map.get(int(row[1]))
        if src is None or dst is None:
            continue
        raw_anchors: Sequence[str] = row[2] or ()
        edges.append(
            LawGraphEdgeRecord(
                source_jurisdiction=src.jurisdiction,
                source_id_scheme=src.id_scheme,
                source_local_id=src.local_id,
                target_jurisdiction=dst.jurisdiction,
                target_id_scheme=dst.id_scheme,
                target_local_id=dst.local_id,
                kind="sub_delegation" if src.norm_kind == "regulation" else "delegation",
                anchor_articles=tuple(_format_cited_path(str(a)) for a in raw_anchors),
                all_rooted=bool(row[3]),
            )
        )
    return edges


def _child_groups(
    focus_id: int,
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
) -> tuple[ChildGroupCountRecord, ...]:
    counts: Counter[str] = Counter()
    for cid in children_of.get(focus_id, ()):
        if cid in res_info:
            counts[res_info[cid][1]] += 1
    return tuple(
        ChildGroupCountRecord(document_type=dt, count=cnt)
        for dt, cnt in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
