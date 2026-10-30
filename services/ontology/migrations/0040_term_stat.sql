-- 0040_term_stat — 字面臂略過高頻詞：詞頻表與查詢詞修剪
--
-- 整句問題分詞後以 OR 連成 tsquery，「之」「應」這類幾乎每段都有的詞會讓候選集成為數萬到
-- 數十萬列，ts_rank_cd 要對每一列算分，延遲因此與候選集成正比，與答案無關。高頻詞幾乎沒有
-- 鑑別力：略過它們，字面臂的候選只剩有鑑別力的詞命中的段落。
--
-- 詞頻（含有該詞的段落數）隨投影重算，與 retrieval.passage.tokenizer 同一個道理：識別碼是
-- 欄位不是身分，同一時間只留現行分詞器的統計，換詞典就整批換掉。查不到統計（尚未重算，或分詞器
-- 不符）時不修剪，查詢照舊，只是慢。

CREATE TABLE retrieval.term_stat (
    tokenizer text NOT NULL CHECK (tokenizer <> ''),
    term      text NOT NULL CHECK (term <> ''),
    doc_freq  integer NOT NULL CHECK (doc_freq > 0),
    PRIMARY KEY (tokenizer, term)
);

CREATE TABLE retrieval.term_stat_total (
    tokenizer text PRIMARY KEY CHECK (tokenizer <> ''),
    passages  bigint NOT NULL CHECK (passages >= 0)
);

COMMENT ON TABLE retrieval.term_stat IS '各詞出現在幾個段落（retrieval.passage 的列）；只含現行分詞器，由 retrieval.refresh_term_stat 整批重算。';
COMMENT ON TABLE retrieval.term_stat_total IS '計算 term_stat 時該分詞器的段落總數：詞頻比例的分母。';

-- 整批重算現行分詞器的詞頻，並丟掉其他分詞器的統計。一個交易：查詢看到的不是舊的整批就是
-- 新的整批。回傳詞數。
CREATE FUNCTION retrieval.refresh_term_stat(p_tokenizer text) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM retrieval.term_stat;
    DELETE FROM retrieval.term_stat_total;
    INSERT INTO retrieval.term_stat (tokenizer, term, doc_freq)
        SELECT p_tokenizer, s.word, s.ndoc
          FROM ts_stat(format('SELECT tsv FROM retrieval.passage WHERE tokenizer = %L', p_tokenizer)) AS s;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO retrieval.term_stat_total (tokenizer, passages)
        SELECT p_tokenizer, count(*) FROM retrieval.passage WHERE tokenizer = p_tokenizer;
    RETURN n;
END
$$;

-- 略過出現在超過 p_max_df（段落比例）的詞；全是高頻詞時留下最稀有的一個，不讓查詢變空。
-- 統計裡沒有的詞當作罕見而保留。p_max_df 為空或不為正、或沒有該分詞器的統計時原樣回傳。
-- 詞素與索引端一致：'simple' 設定下是小寫。
CREATE FUNCTION retrieval.prune_terms(p_terms text, p_tokenizer text, p_max_df double precision)
RETURNS text
LANGUAGE sql STABLE
AS $$
    WITH total AS (
        SELECT passages FROM retrieval.term_stat_total WHERE tokenizer = p_tokenizer
    ),
    w AS (
        SELECT t.word, t.ord, coalesce(s.doc_freq, 0) AS df
          FROM unnest(string_to_array(btrim(p_terms), ' ')) WITH ORDINALITY AS t(word, ord)
          LEFT JOIN retrieval.term_stat AS s
                 ON s.tokenizer = p_tokenizer AND s.term = lower(t.word)
         WHERE btrim(t.word) <> ''
    ),
    common AS (
        SELECT w.word, w.ord FROM w CROSS JOIN total WHERE w.df <= p_max_df * total.passages
    ),
    rarest AS (
        SELECT w.word, w.ord FROM w ORDER BY w.df, w.ord LIMIT 1
    )
    SELECT CASE
        WHEN p_max_df IS NULL OR p_max_df <= 0 OR NOT EXISTS (SELECT 1 FROM total) THEN p_terms
        ELSE coalesce(
            (SELECT string_agg(k.word, ' ' ORDER BY k.ord)
               FROM (SELECT * FROM common
                     UNION ALL
                     SELECT * FROM rarest WHERE NOT EXISTS (SELECT 1 FROM common)) AS k),
            p_terms)
    END
$$;
