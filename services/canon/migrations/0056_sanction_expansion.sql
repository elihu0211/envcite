-- 0056_sanction_expansion — 命中義務條（或依其授權訂定的命令）時帶出處罰它的罰則條
--
-- 以案情描述問「依哪條裁罰」的問句，語意上接近的是被違反的義務（常在授權命令的條文裡），不是罰則條：
-- 罰則條只寫「違反第三十一條第一項第三款…者，處…罰鍰」，沒有行為的描述。檢索命中義務條時，處罰它的
-- 罰則條才是答案。
--
-- - `retrieval.sanctions_for`：某法某條在某日的罰則條，取自處罰依賴圖 `annotation.current_violation`
--   （0055）：罰則單元所罰的條就是該條；目標法規是命令時，沿 `annotation.delegations_at` 往上走到授權它的條
--   （命令授權的命令也算），處罰違反依該條所定命令者（`delegated_rule`）的罰則條一併帶出。
--   只認已確認或規則推導的授權，罰則單元須是該日現行的文字版本。
-- - `retrieval.search_expanded`：`search_hybrid` 的結果依名次逐條放入，每條之後緊接它帶出的罰則條（已在
--   前面的不重複）。帶出的條沒有自己的檢索分數，分數取帶出它的那一條：它是因為那一條相關才相關。

CREATE FUNCTION retrieval.sanctions_for(p_resource bigint, p_article text, p_language text, p_on date)
RETURNS TABLE(resource_id bigint, article_path text)
LANGUAGE sql STABLE
AS $$
    WITH RECURSIVE target(resource_id, article_path) AS (
        SELECT p_resource, p_article
        UNION
        SELECT d.law_resource_id, lp.article_path
          FROM target AS t
         CROSS JOIN LATERAL annotation.delegations_at(t.resource_id, p_language, p_on) AS d
          JOIN corpus.provision AS lp ON lp.id = d.law_provision_id
    )
    SELECT DISTINCT pp.resource_id, pp.article_path
      FROM target AS t
      JOIN corpus.provision AS cp ON cp.resource_id = t.resource_id AND cp.article_path = t.article_path
      JOIN annotation.current_violation AS vi ON vi.cited_provision_id = cp.id
      JOIN corpus.provision AS pp ON pp.id = vi.provision_id
      JOIN corpus.provision_version AS v
        ON v.provision_id = vi.provision_id AND v.content_sha256 = vi.content_sha256
       AND v.superseded_at IS NULL AND v.force_period @> p_on AND v.language = p_language
     WHERE NOT (pp.resource_id = t.resource_id AND pp.article_path = t.article_path)
$$;

CREATE FUNCTION retrieval.search_expanded(
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
                lexical_score real, semantic_similarity real, rank integer)
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
        SELECT s.resource_id, s.article_path, h.lexical_score, h.semantic_similarity, h.r, 1
          FROM hit AS h
         CROSS JOIN LATERAL retrieval.sanctions_for(h.resource_id, h.path, p_language, p_as_of) AS s
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
