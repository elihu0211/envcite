-- 0007_sanction_chain — 義務條的處罰鏈：混合檢索與函釋入口用同一份展開，並走到處罰的處罰
--
-- 命中義務條時，檢索把處罰它的罰則條一併帶出（`annotation.current_violation`）。兩個缺口：
--   1. 只展開混合檢索自己的命中。函釋入口（`retrieval.guidance_articles`）帶出的條文不展開：
--      「露天燃燒」條文裡沒有這四個字，靠函釋找到第 32 條之後，處罰它的第 67 條就帶不出來。
--   2. 只展開一層。第 56 條罰的是「不遵行第 67 條第 2 項停止作為命令」，是罰則的罰則；
--      義務（32）→ 罰鍰（67）→ 不遵行命令的刑責（56）是一條完整的答案。
--
-- `sanction_chain` 取代只走一層的 `sanctions_for`：沿罰則往下走（含層數，上限 3，同一條取最小層數），
-- 兩處展開都用它。展開出來的條緊跟在來源之後，層數小的在前。
--
-- 遞迴寫在同一個查詢裡，不是函式呼叫函式：遞迴項裡的 SQL 函式會被內嵌，規劃器推不下條件，
-- 單次呼叫從約 30 毫秒變成約 450 毫秒（每個命中都要呼叫一次）。

CREATE FUNCTION retrieval.sanction_chain(p_resource bigint, p_article text, p_language text, p_on date)
RETURNS TABLE(resource_id bigint, article_path text, depth integer)
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE target(resource_id, article_path) AS (
        SELECT p_resource, p_article
        UNION
        SELECT d.law_resource_id, lp.article_path
          FROM target AS t
         CROSS JOIN LATERAL annotation.delegations_at(t.resource_id, p_language, p_on) AS d
          JOIN corpus.provision AS lp ON lp.id = d.law_provision_id
    ),
    chain(resource_id, article_path, depth) AS (
        SELECT pp.resource_id, pp.article_path, 1
          FROM target AS t
          JOIN corpus.provision AS cp ON cp.resource_id = t.resource_id AND cp.article_path = t.article_path
          JOIN annotation.current_violation AS vi ON vi.cited_provision_id = cp.id
          JOIN corpus.provision AS pp ON pp.id = vi.provision_id
          JOIN corpus.provision_version AS v
            ON v.provision_id = vi.provision_id AND v.content_sha256 = vi.content_sha256
           AND v.superseded_at IS NULL AND v.force_period @> p_on AND v.language = p_language
         WHERE NOT (pp.resource_id = t.resource_id AND pp.article_path = t.article_path)
        UNION
        SELECT pp.resource_id, pp.article_path, c.depth + 1
          FROM chain AS c
          JOIN corpus.provision AS cp ON cp.resource_id = c.resource_id AND cp.article_path = c.article_path
          JOIN annotation.current_violation AS vi ON vi.cited_provision_id = cp.id
          JOIN corpus.provision AS pp ON pp.id = vi.provision_id
          JOIN corpus.provision_version AS v
            ON v.provision_id = vi.provision_id AND v.content_sha256 = vi.content_sha256
           AND v.superseded_at IS NULL AND v.force_period @> p_on AND v.language = p_language
         WHERE c.depth < 3 AND NOT (pp.resource_id = c.resource_id AND pp.article_path = c.article_path)
    )
    SELECT c.resource_id, c.article_path, min(c.depth)::integer
      FROM chain AS c
     WHERE NOT (c.resource_id = p_resource AND c.article_path = p_article)
     GROUP BY c.resource_id, c.article_path
$$;

COMMENT ON FUNCTION retrieval.sanction_chain(p_resource bigint, p_article text, p_language text, p_on date) IS '義務條的處罰鏈：處罰它的罰則條（第 1 層；命令則含其授權條文的罰則），與處罰這些罰則的條（第 2、3 層，如不遵行命令的刑責）；層數是最小的那一層。';

CREATE OR REPLACE FUNCTION retrieval.search_expanded(p_terms text, p_tokenizer text, p_embedding public.vector, p_model text, p_as_of date, p_language text, p_limit integer, p_pool integer, p_resources bigint[]) RETURNS TABLE(version_id bigint, resource_id bigint, path text, lexical_score real, semantic_similarity real, rank integer)
    LANGUAGE sql STABLE
    AS $$
    WITH hit AS MATERIALIZED (
        SELECT h.*, row_number() OVER (ORDER BY h.rank_score DESC, h.version_id) AS r
          FROM retrieval.search_hybrid(p_terms, p_tokenizer, p_embedding, p_model, p_as_of,
                                       p_language, p_limit, p_pool, p_resources) AS h
    ),
    candidate AS (
        SELECT h.resource_id, h.path, h.lexical_score, h.semantic_similarity, h.r, 0 AS k
          FROM hit AS h
        UNION ALL
        SELECT s.resource_id, s.article_path, h.lexical_score, h.semantic_similarity, h.r, s.depth
          FROM hit AS h
         CROSS JOIN LATERAL retrieval.sanction_chain(h.resource_id, h.path, p_language, p_as_of) AS s
    ),
    first AS (
        SELECT DISTINCT ON (c.resource_id, c.path) c.*
          FROM candidate AS c
         ORDER BY c.resource_id, c.path, c.r, c.k
    )
    SELECT av.id, f.resource_id, f.path, f.lexical_score, f.semantic_similarity,
           (row_number() OVER (ORDER BY f.r, f.k, f.resource_id, f.path))::integer
      FROM first AS f
      JOIN corpus.provision AS ap ON ap.resource_id = f.resource_id AND ap.path = f.path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY f.r, f.k, f.resource_id, f.path
     LIMIT p_limit
$$;

CREATE OR REPLACE FUNCTION retrieval.guidance_articles(p_phrases text[], p_as_of date, p_language text, p_limit integer, p_max_df double precision) RETURNS TABLE(version_id bigint, resource_id bigint, path text, documents integer)
    LANGUAGE sql STABLE
    AS $$
    WITH eligible AS (
        SELECT d.id, d.subject, d.body
          FROM guidance.document AS d
         WHERE d.issued_on <= p_as_of
           AND NOT EXISTS (SELECT 1 FROM guidance.cessation AS c
                            WHERE c.document_id = d.id AND c.ceased_on <= p_as_of)
           AND EXISTS (SELECT 1 FROM guidance.interpretation AS i WHERE i.document_id = d.id)
    ),
    matching AS (
        SELECT e.id
          FROM eligible AS e
         WHERE cardinality(p_phrases) > 0
           AND NOT EXISTS (SELECT 1 FROM unnest(p_phrases) AS k
                            WHERE position(k IN e.subject) = 0 AND position(k IN e.body) = 0)
    ),
    docs AS (
        SELECT m.id
          FROM matching AS m
         WHERE p_max_df <= 0
            OR (SELECT count(*) FROM matching)::double precision
               <= p_max_df * (SELECT count(*) FROM eligible)
    ),
    anchored AS (
        SELECT p.resource_id, p.article_path, count(DISTINCT docs.id)::integer AS documents
          FROM docs
          JOIN guidance.interpretation AS i ON i.document_id = docs.id
          JOIN corpus.provision_version AS v ON v.provision_id = i.provision_id
                                            AND v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
    ),
    -- 函釋解釋的條，次序同原本：提到它的函釋多的在前
    found AS (
        SELECT av.id AS version_id, a.resource_id, a.article_path, a.documents,
               (row_number() OVER (ORDER BY a.documents DESC, av.id))::integer AS pos
          FROM anchored AS a
          JOIN corpus.provision AS ap ON ap.resource_id = a.resource_id AND ap.path = a.article_path
          JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                             AND av.superseded_at IS NULL AND av.force_period @> p_as_of
          JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
    ),
    -- 義務條連同處罰它的條（處罰鏈）：和混合檢索命中義務條時同一份展開，緊跟在來源之後
    candidate AS (
        SELECT f.version_id, f.resource_id, f.article_path, f.documents, f.pos, 0 AS depth
          FROM found AS f
        UNION ALL
        SELECT sv.id, s.resource_id, s.article_path, 0, f.pos, s.depth
          FROM found AS f
         CROSS JOIN LATERAL retrieval.sanction_chain(f.resource_id, f.article_path,
                                                     p_language, p_as_of) AS s
          JOIN corpus.provision AS sp ON sp.resource_id = s.resource_id AND sp.path = s.article_path
          JOIN corpus.provision_version AS sv ON sv.provision_id = sp.id
                                             AND sv.superseded_at IS NULL AND sv.force_period @> p_as_of
          JOIN corpus.legal_expression AS se ON se.id = sv.expression_id AND se.language = p_language
    ),
    first AS (
        -- 同一條可能既是函釋直接解釋的、又是另一條的處罰條：留它自己的那一列（帶函釋件數）
        SELECT DISTINCT ON (c.version_id) c.*
          FROM candidate AS c
         ORDER BY c.version_id, (c.depth = 0) DESC, c.pos, c.depth
    )
    SELECT f.version_id, f.resource_id, f.article_path, f.documents
      FROM first AS f
     ORDER BY f.pos, f.depth, f.version_id
     LIMIT p_limit
$$;

DROP FUNCTION retrieval.sanctions_for(bigint, text, text, date);
