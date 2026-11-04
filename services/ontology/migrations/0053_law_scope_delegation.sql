-- 0053_law_scope_delegation — 點名法規的檢索範圍納入依其授權訂定的命令
--
-- 問句點名母法（「依水污染防治法…應如何申報」）時，答案常在依該法授權訂定的命令（施行細則、管理辦法、
-- 標準）裡；0047 的範圍只含被點名的法規本身，這些命令反而被排除在外。範圍改為被點名的法規加上沿
-- `annotation.delegated_by` 遞迴取得、在查詢日有效的授權命令（命令授權的命令也算）。
--
-- 只認已確認或規則推導的授權（`delegated_by` 讀 `delegation_usable`）；授權條文本身是否現行不影響範圍：
-- 命令仍有效就屬於這部法的規範體系。遞迴以 UNION 去重，授權成環也會停。

CREATE FUNCTION retrieval.law_scope(p_laws text[], p_language text, p_on date)
RETURNS bigint[]
LANGUAGE sql STABLE
AS $$
    WITH RECURSIVE scope(id) AS (
        SELECT r.id
          FROM corpus.legal_resource AS r
         WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(p_laws)
        UNION
        SELECT d.command_resource_id
          FROM scope AS s
         CROSS JOIN LATERAL annotation.delegated_by(s.id, p_language, p_on) AS d
    )
    SELECT array_agg(id ORDER BY id) FROM scope
$$;

COMMENT ON FUNCTION retrieval.law_scope(text[], text, date) IS
    '法規代碼 → 檢索範圍（法規內部編號）：這些法規與依其授權（遞迴）訂定、在該日有效的命令。';

-- 範圍是呼叫端傳入的運算式（`law_scope(...)`）時，SQL 函式內嵌會把運算式代入每一處使用 `p_resources` 的地方，
-- 在每一個候選列各算一次遞迴授權鏈（單題數分鐘）。範圍先在 MATERIALIZED 的 `scope` 算一次，兩臂都從它取，
-- 與傳入的是字面陣列或運算式無關。簽章與結果不變。

CREATE OR REPLACE FUNCTION retrieval.search_hybrid(
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
    WITH scope AS MATERIALIZED (SELECT p_resources AS ids),
    q AS MATERIALIZED (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    lex AS (
        SELECT p.resource_id, p.article_path, max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.passage AS i
         CROSS JOIN q
          JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE i.tokenizer = p_tokenizer
           AND i.tsv @@ q.query
           AND ((SELECT ids FROM scope) IS NULL OR p.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
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
         WHERE (SELECT ids FROM scope) IS NOT NULL AND p_embedding IS NOT NULL AND i.model = p_model
           AND i.content_sha256 IN (SELECT v.content_sha256
                                      FROM corpus.provision_version AS v
                                     WHERE v.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
    ),
    near AS (
        (SELECT i.content_sha256, 1 - (i.embedding <=> p_embedding) AS similarity
           FROM retrieval.passage AS i
          WHERE (SELECT ids FROM scope) IS NULL AND p_embedding IS NOT NULL AND i.model = p_model
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
         WHERE ((SELECT ids FROM scope) IS NULL OR p.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
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
