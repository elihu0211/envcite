-- 0002_retrieval — 檢索投影：條的分詞全文索引
--
-- 可重建的投影，不是語料的真值（02-04 §8）。分詞在資料庫之外做，索引與查詢須用
-- 同一個分詞器（04-01 §3、§5）。稠密檢索臂待嵌入模型選定後以新遷移加入。

CREATE SCHEMA retrieval;

CREATE TABLE retrieval.article_index (
    provision_version_id bigint PRIMARY KEY REFERENCES corpus.provision_version ON DELETE CASCADE,
    content_tsv          tsvector NOT NULL,
    -- 分詞器識別碼：實作版本加詞典內容雜湊。不同分詞器的詞素不可比。
    tokenizer            text NOT NULL CHECK (tokenizer <> '')
);

-- 標準 GIN 索引：隨串流複寫到每個副本，備援切換後不必重建。
CREATE INDEX article_index_tsv ON retrieval.article_index USING gin (content_tsv);

-- 已分詞、以空白分隔的查詢詞轉為 OR 連接的 tsquery。逐詞以 plainto_tsquery 組成，
-- 輸入被跳脫而非解析。OR 而非 AND：多數實際問題不會含有條文的每一個詞。
CREATE FUNCTION retrieval.terms_to_tsquery(terms text) RETURNS tsquery
    LANGUAGE sql IMMUTABLE STRICT
    RETURN coalesce(
        (
            SELECT string_agg(plainto_tsquery('simple', w)::text, ' | ')
              FROM unnest(string_to_array(btrim(terms), ' ')) AS w
             WHERE btrim(w) <> ''
               AND plainto_tsquery('simple', w)::text <> ''
        )::tsquery,
        ''::tsquery
    );

-- 某日有效之條，依字面相關性排序。
--   rank_score：名次倒數融合的分數，只供排序，不表示相關性（04-01 §6）。
--   lexical_score：原始相關性（ts_rank_cd），判斷「有無答案」應看這一欄。
-- 只納入該日效力區間內、文字可得的條；該日無有效版本的法規不以現行文字頂替。
CREATE FUNCTION retrieval.search_articles(
    p_terms     text,
    p_tokenizer text,
    p_as_of     date,
    p_language  text,
    p_limit     integer
)
RETURNS TABLE (
    version_id    bigint,
    resource_id   bigint,
    path          text,
    rank_score    numeric,
    lexical_score real
)
LANGUAGE sql STABLE
AS $$
    SELECT v.id, v.resource_id, p.path,
           1.0 / (60 + RANK() OVER (ORDER BY ts_rank_cd(i.content_tsv, q.query) DESC, v.id)),
           ts_rank_cd(i.content_tsv, q.query)
      FROM retrieval.article_index AS i
      JOIN corpus.provision_version AS v ON v.id = i.provision_version_id
      JOIN corpus.provision AS p ON p.id = v.provision_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     CROSS JOIN (SELECT retrieval.terms_to_tsquery(p_terms) AS query) AS q
     WHERE i.content_tsv @@ q.query
       AND i.tokenizer = p_tokenizer
       AND p.provision_type = 'article'
       AND v.in_force @> p_as_of
       AND e.language = p_language
       AND e.text_status = 'available'
     ORDER BY ts_rank_cd(i.content_tsv, q.query) DESC, v.id
     LIMIT p_limit;
$$;
