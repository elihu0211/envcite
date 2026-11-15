-- 0045_hybrid_query_once — 混合檢索的查詢 tsquery 只算一次
--
-- 0039 的 `q` CTE 只被引用一次，PostgreSQL 因此把它內嵌進 `lex`：`terms_to_tsquery(prune_terms(…))`
-- 在 `ts_rank_cd` 裡對每一個候選列重算一次，而 `prune_terms` 要查詞頻表。全庫（65 萬段）下候選
-- 約萬列，算分的時間大半花在重複建 tsquery，不在 `ts_rank_cd` 本身。
-- 加 MATERIALIZED 讓它成為只算一次的查詢常數；函式的語意、輸入、輸出與 0039 相同，
-- 分數與名次逐位元不變（同一個 tsquery、同一個 `ts_rank_cd`）。

CREATE OR REPLACE FUNCTION retrieval.search_hybrid(
    p_terms     text,
    p_tokenizer text,
    p_embedding vector,
    p_model     text,
    p_as_of     date,
    p_language  text,
    p_limit     integer,
    p_pool      integer
) RETURNS TABLE(version_id bigint, resource_id bigint, path text,
                lexical_score real, semantic_similarity real, rank_score double precision)
LANGUAGE sql STABLE
AS $$
    WITH q AS MATERIALIZED (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    lex AS (
        SELECT p.resource_id, p.article_path, max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.passage AS i
         CROSS JOIN q
          JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE i.tokenizer = p_tokenizer
           AND i.tsv @@ q.query
           AND v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
         ORDER BY score DESC, p.resource_id, p.article_path
         LIMIT p_pool
    ),
    near AS (
        SELECT i.content_sha256, 1 - (i.embedding <=> p_embedding) AS similarity
          FROM retrieval.passage AS i
         WHERE p_embedding IS NOT NULL AND i.model = p_model
         ORDER BY i.embedding <=> p_embedding
         LIMIT p_pool * 4
    ),
    sem AS (
        SELECT p.resource_id, p.article_path, max(n.similarity) AS score
          FROM near AS n
          JOIN corpus.provision_version AS v ON v.content_sha256 = n.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
         ORDER BY score DESC, p.resource_id, p.article_path
         LIMIT p_pool
    ),
    fused AS (
        SELECT coalesce(l.resource_id, s.resource_id) AS resource_id,
               coalesce(l.article_path, s.article_path) AS article_path,
               l.score AS lexical, s.score AS semantic,
               coalesce(1.0 / (60 + l.r), 0) + coalesce(1.0 / (60 + s.r), 0) AS rank_score
          FROM (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r FROM lex) AS l
          FULL JOIN (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r FROM sem) AS s
            ON s.resource_id = l.resource_id AND s.article_path = l.article_path
    )
    SELECT av.id, f.resource_id, f.article_path,
           coalesce(f.lexical, 0)::real, f.semantic::real, f.rank_score::double precision
      FROM fused AS f
      JOIN corpus.provision AS ap ON ap.resource_id = f.resource_id AND ap.path = f.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY f.rank_score DESC, av.id
     LIMIT p_limit
$$;
