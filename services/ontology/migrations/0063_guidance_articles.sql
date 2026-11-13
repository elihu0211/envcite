-- 0063_guidance_articles — 函釋提到查詢詞時，帶出它所解釋的條文
--
-- 條文寫「燃燒」、函釋寫「露天燃燒」：使用者查的詞常常是函釋的用語，不是條文的。這時兩個檢索臂都找不到
-- 對的條文（查詢詞沒有出現在任何條文裡，語意相似度也低於下界），但解釋它的函釋已經指明是哪一條。
--
-- 函釋不是推論的前提（行政程序法第 159 條），這裡只拿它找條文：回傳的是條文，不是函釋；
-- 錨定與停止適用的判斷與 `guidance.for_statements` 相同——函釋解釋的正是那段文字（錨定的內容雜湊在查詢日
-- 仍是現行版本），發文日不晚於查詢日，且在查詢日尚未停止適用。
--
-- 每個詞組都要出現在函釋的主旨或內文（子字串，不分詞）；只看至少有一個錨定的函釋，沒有錨定的函釋找不到條文。
-- 回傳每條被幾件函釋帶出，多的在前。

CREATE FUNCTION retrieval.guidance_articles(
    p_phrases  text[],
    p_as_of    date,
    p_language text,
    p_limit    integer
) RETURNS TABLE(version_id bigint, resource_id bigint, path text, documents integer)
LANGUAGE sql STABLE
AS $$
    WITH docs AS (
        SELECT d.id
          FROM guidance.document AS d
         WHERE cardinality(p_phrases) > 0
           AND d.issued_on <= p_as_of
           AND NOT EXISTS (SELECT 1 FROM guidance.cessation AS c
                            WHERE c.document_id = d.id AND c.ceased_on <= p_as_of)
           AND EXISTS (SELECT 1 FROM guidance.interpretation AS i WHERE i.document_id = d.id)
           AND NOT EXISTS (SELECT 1 FROM unnest(p_phrases) AS k
                            WHERE position(k IN d.subject) = 0 AND position(k IN d.body) = 0)
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

COMMENT ON FUNCTION retrieval.guidance_articles(text[], date, text, integer) IS
    '詞組 → 函釋（主旨或內文含每個詞組、在該日有效）所解釋的條文，被越多件函釋帶出的在前。函釋只用來找條文，不是依據。';
