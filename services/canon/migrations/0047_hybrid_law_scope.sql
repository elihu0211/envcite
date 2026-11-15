-- 0047_hybrid_law_scope — 混合檢索可限定在指定的法規
--
-- 問句點名了法規（「水污染防治法第26條…」）時，全庫兩臂的候選池被 65 萬段的其他法規稀釋：期望條文
-- 不在任一臂的前 50 條。限定在被點名的法規之後，同一題庫的 R@10 由 0.232 升到 0.308。
-- 新參數 `p_resources`（法規內部編號）非 NULL 時兩臂的候選都只取這些法規；NULL 時與 0045 逐位元相同。
--
-- 語意臂在範圍內改成精確掃描：HNSW 只能先取最近鄰再過濾，範圍很小時過濾後所剩無幾。範圍是被點名的
-- 少數幾部法規（至多數千段），精確掃描的成本可忽略。
--
-- 舊簽章直接刪除，呼叫端（`store.py`）同一次改；不留八個參數的版本。

DROP FUNCTION retrieval.search_hybrid(text, text, vector, text, date, text, integer, integer);

CREATE FUNCTION retrieval.search_hybrid(
    p_terms     text,
    p_tokenizer text,
    p_embedding vector,
    p_model     text,
    p_as_of     date,
    p_language  text,
    p_limit     integer,
    p_pool      integer,
    p_resources bigint[]
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
           AND (p_resources IS NULL OR p.resource_id = ANY(p_resources))
           AND v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
         ORDER BY score DESC, p.resource_id, p.article_path
         LIMIT p_pool
    ),
    in_scope AS MATERIALIZED (
        SELECT i.content_sha256, i.embedding
          FROM retrieval.passage AS i
         WHERE p_resources IS NOT NULL AND p_embedding IS NOT NULL AND i.model = p_model
           AND i.content_sha256 IN (SELECT v.content_sha256
                                      FROM corpus.provision_version AS v
                                     WHERE v.resource_id = ANY(p_resources))
    ),
    near AS (
        (SELECT i.content_sha256, 1 - (i.embedding <=> p_embedding) AS similarity
           FROM retrieval.passage AS i
          WHERE p_resources IS NULL AND p_embedding IS NOT NULL AND i.model = p_model
          ORDER BY i.embedding <=> p_embedding
          LIMIT p_pool * 4)
        UNION ALL
        (SELECT s.content_sha256, 1 - (s.embedding <=> p_embedding) AS similarity
           FROM in_scope AS s
          ORDER BY s.embedding <=> p_embedding
          LIMIT p_pool * 4)
    ),
    sem AS (
        SELECT p.resource_id, p.article_path, max(n.similarity) AS score
          FROM near AS n
          JOIN corpus.provision_version AS v ON v.content_sha256 = n.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE (p_resources IS NULL OR p.resource_id = ANY(p_resources))
           AND v.superseded_at IS NULL
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
