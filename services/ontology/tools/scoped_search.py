"""`retrieval.search_hybrid` 的唯讀重現，多一個「候選池之前」的法規範圍條件。

    from scoped_search import scoped_search

服務端的函式不動、不遷移：這裡逐字重現 0039_hybrid_tiebreak.sql 的 `search_hybrid`（同一個
`ts_rank_cd`、`terms_to_tsquery`、候選池與同分次序、名次倒數融合 k = 60）與
`OntologyStore.search` 的外層查詢，只在兩臂取候選的 WHERE 加 `resource_id = ANY(範圍)`。
`scope=None` 時查詢與服務端相同，`tests/test_scoped_search.py` 以同一語料比對兩者的輸出完全相同。

語意臂的 HNSW 近鄰（`near`）不加範圍條件：近似最近鄰只能先排序後過濾，加在其後的 `sem` 才是
候選池；本次範圍內的段落才有嵌入，所以 `near` 本來就只會出範圍內的段落。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from envcite_ontology.embedding import to_sql
from envcite_ontology.store import LANGUAGE, SEARCH_POOL, SearchHit

_SQL = """
WITH q AS (SELECT retrieval.terms_to_tsquery(%(terms)s) AS query),
lex AS (
    SELECT p.resource_id, p.article_path, max(ts_rank_cd(i.tsv, q.query)) AS score
      FROM retrieval.passage AS i
     CROSS JOIN q
      JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
      JOIN corpus.provision AS p ON p.id = v.provision_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE i.tokenizer = %(tokenizer)s
       AND i.tsv @@ q.query
       AND v.superseded_at IS NULL
       AND v.force_period @> %(on)s::date
       AND e.language = %(language)s
       AND e.text_status = 'available'
       {scope}
     GROUP BY p.resource_id, p.article_path
     ORDER BY score DESC, p.resource_id, p.article_path
     LIMIT %(pool)s
),
near AS (
    SELECT i.content_sha256, 1 - (i.embedding <=> %(embedding)s::vector) AS similarity
      FROM retrieval.passage AS i
     WHERE %(embedding)s::vector IS NOT NULL AND i.model = %(model)s
     ORDER BY i.embedding <=> %(embedding)s::vector
     LIMIT %(pool)s * 4
),
sem AS (
    SELECT p.resource_id, p.article_path, max(n.similarity) AS score
      FROM near AS n
      JOIN corpus.provision_version AS v ON v.content_sha256 = n.content_sha256
      JOIN corpus.provision AS p ON p.id = v.provision_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE v.superseded_at IS NULL
       AND v.force_period @> %(on)s::date
       AND e.language = %(language)s
       AND e.text_status = 'available'
       {scope}
     GROUP BY p.resource_id, p.article_path
     ORDER BY score DESC, p.resource_id, p.article_path
     LIMIT %(pool)s
),
fused AS (
    SELECT coalesce(l.resource_id, s.resource_id) AS resource_id,
           coalesce(l.article_path, s.article_path) AS article_path,
           l.score AS lexical, s.score AS semantic,
           coalesce(1.0 / (60 + l.r), 0) + coalesce(1.0 / (60 + s.r), 0) AS rank_score
      FROM (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r
              FROM lex) AS l
      FULL JOIN (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r
                   FROM sem) AS s
        ON s.resource_id = l.resource_id AND s.article_path = l.article_path
),
s AS (
    SELECT av.id AS version_id, f.resource_id, f.article_path AS path,
           coalesce(f.lexical, 0)::real AS lexical_score, f.semantic::real AS semantic_similarity,
           f.rank_score::double precision AS rank_score
      FROM fused AS f
      JOIN corpus.provision AS ap ON ap.resource_id = f.resource_id AND ap.path = f.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL
                                         AND av.force_period @> %(on)s::date
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id
                                        AND ae.language = %(language)s
     ORDER BY f.rank_score DESC, av.id
     LIMIT %(limit)s
)
SELECT corpus.version_iri(s.version_id), r.local_id, e.title,
       corpus.article_number(s.path), corpus.article_text(s.version_id),
       s.lexical_score, s.semantic_similarity
  FROM s
  JOIN corpus.legal_resource AS r ON r.id = s.resource_id
  JOIN corpus.provision_version AS v ON v.id = s.version_id
  JOIN corpus.legal_expression AS e ON e.id = v.expression_id
 ORDER BY s.rank_score DESC, s.version_id
"""

_IN_SCOPE = "AND p.resource_id = ANY(%(scope)s)"


def resource_ids(conn: psycopg.Connection[Any], pcodes: Sequence[str]) -> list[int]:
    return [
        int(i)
        for (i,) in conn.execute(
            "SELECT id FROM corpus.legal_resource"
            " WHERE id_scheme = 'moj_pcode' AND local_id = ANY(%s)",
            (list(pcodes),),
        )
    ]


def scoped_search(
    conn: psycopg.Connection[Any],
    terms: str,
    *,
    tokenizer: str,
    as_of: str = "",
    limit: int = 10,
    embedding: Sequence[float] | None = None,
    model: str = "",
    scope: Sequence[int] | None = None,
) -> list[SearchHit]:
    """與 `OntologyStore.search` 同簽章；`scope`（內部法規編號）給定時兩臂的候選池只含範圍內。"""
    fragment = _IN_SCOPE if scope is not None else ""
    rows = conn.execute(
        _SQL.format(scope=fragment),
        {
            "terms": terms,
            "tokenizer": tokenizer,
            "on": as_of or "today",
            "language": LANGUAGE,
            "limit": limit,
            "embedding": None if embedding is None else to_sql(embedding),
            "model": model,
            "pool": SEARCH_POOL,
            "scope": None if scope is None else list(scope),
        },
    ).fetchall()
    return [
        SearchHit(
            iri=iri,
            law_id=law_id,
            law_title=title,
            number=number,
            content=content,
            lexical_score=lexical,
            semantic_similarity=semantic,
        )
        for iri, law_id, title, number, content, lexical, semantic in rows
    ]
