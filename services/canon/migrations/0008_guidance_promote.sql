-- 0008_guidance_promote — 函釋入口：既是函釋直接解釋、又是別條的處罰條的條，放在最前面的那個位置
--
-- 混合檢索展開時，同一條出現兩次就取名次最前的那個（`search_expanded` 的 DISTINCT ON … ORDER BY r, k）。
-- 函釋入口的展開沒有照這條規則：一條若既被函釋直接解釋、又是名次靠前的來源的處罰條，只留它自己的那一列，
-- 排在它自己的件數那個位置；來源後面緊跟的處罰位置被占掉，這條就落到候選截斷線之外。
-- 例：第 7 條的處罰條第 40 條只被 1 件函釋解釋，位置約在第 22，候選只取 15 條，處罰鏈裡的它就消失了。
--
-- 改成取名次最前的那一列，件數仍是它被函釋直接解釋的件數（不因展開而變成 0）。

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
        -- 同一條出現兩次（直接解釋的一列、處罰條的一列）：取最前面的位置，與 `search_expanded` 相同；
        -- 件數取它被函釋直接解釋的件數，展開的列件數是 0
        SELECT DISTINCT ON (c.version_id) c.version_id, c.resource_id, c.article_path,
               max(c.documents) OVER (PARTITION BY c.version_id) AS documents, c.pos, c.depth
          FROM candidate AS c
         ORDER BY c.version_id, c.pos, c.depth
    )
    SELECT f.version_id, f.resource_id, f.article_path, f.documents
      FROM first AS f
     ORDER BY f.pos, f.depth, f.version_id
     LIMIT p_limit
$$;
