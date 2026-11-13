-- 0064_guidance_articles_max_df — 出現在太多函釋裡的詞組不當入口
--
-- 0063 的入口只看函釋有沒有提到詞組，詞越常見，帶出的條文越雜：在 staging，「固定污染源」占可用函釋的
-- 近四分之一、「水污染防治」一成多，帶出的條文把列表佔滿，其中多數與使用者要找的條文無關。有鑑別力的
-- 詞組（「露天燃燒」「噪音」「繞流排放」）只占約 1%，兩邊相差一個數量級。
--
-- 命中的函釋占可用函釋（查詢日已發文、尚未停止適用、至少有一個錨定）的比例超過 `p_max_df` 時，這個查詢
-- 的函釋入口整個不啟用，不挑其中幾件：一個詞組出現在那麼多函釋裡，就說不出它指向哪幾條。`p_max_df` 為 0
-- 時不限制。比例與字面臂略過高頻詞（`retrieval.prune_terms`）是同一個想法，分母是這一軸自己的可用函釋。
--
-- 簽章多一個參數，舊的移除：兩者並存時以位置參數呼叫會有歧義。

DROP FUNCTION retrieval.guidance_articles(text[], date, text, integer);

CREATE FUNCTION retrieval.guidance_articles(
    p_phrases  text[],
    p_as_of    date,
    p_language text,
    p_limit    integer,
    p_max_df   double precision
) RETURNS TABLE(version_id bigint, resource_id bigint, path text, documents integer)
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
    )
    SELECT av.id, a.resource_id, a.article_path, a.documents
      FROM anchored AS a
      JOIN corpus.provision AS ap ON ap.resource_id = a.resource_id AND ap.path = a.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY a.documents DESC, av.id
     LIMIT p_limit
$$;

COMMENT ON FUNCTION retrieval.guidance_articles(text[], date, text, integer, double precision) IS
    '詞組 → 函釋（主旨或內文含每個詞組、在該日有效）所解釋的條文，被越多件函釋帶出的在前。命中的函釋占可用函釋的比例超過 p_max_df 時不回任何條（0 為不限制）。函釋只用來找條文，不是依據。';
