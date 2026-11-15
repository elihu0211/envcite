-- 0032_passage — 檢索投影收斂成一張表：一段文字一列，字面臂與語意臂是同一列的兩欄
--
-- 原本字面臂以（文字雜湊，分詞器）為鍵，設計中的語意臂另以（文字雜湊，模型）為鍵另立一張表。
-- 同一段文字永遠只有一個現行分詞器、一個現行模型：舊識別碼的列重建時就刪掉，從不並存，
-- 所以識別碼是欄位，不是身分。合成一列之後：
--
-- - 「缺列」只剩一個條件（分詞器不符，或模型不符、尚未嵌入），一個清理函式；
-- - 分詞器或模型換了，就地更新那一欄，另一臂不必重算：換詞典不會觸發重新嵌入（最貴的一步）；
-- - 查詢只連回版本列一次。
--
-- 鍵仍是段落文字的雜湊（內容定址，無從陳舊），不指向版本列。
-- pgvector 不是 trusted 擴充：正式環境由部署端的 CloudNativePG Database 資源建立，這裡的
-- IF NOT EXISTS 屆時略過；測試與本機開發以超級使用者連線，由這裡建立。
-- search_articles 與 missing_lexical 由 search_hybrid 與投影程式取代，移除。

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE retrieval.lexical_index RENAME TO passage;
ALTER INDEX retrieval.lexical_index_tsv RENAME TO passage_tsv;
ALTER TABLE retrieval.passage RENAME CONSTRAINT lexical_index_content_sha256_check TO passage_content_sha256_check;
ALTER TABLE retrieval.passage RENAME CONSTRAINT lexical_index_tokenizer_check TO passage_tokenizer_check;
ALTER TABLE retrieval.passage RENAME CONSTRAINT lexical_index_content_sha256_not_null TO passage_content_sha256_not_null;
ALTER TABLE retrieval.passage RENAME CONSTRAINT lexical_index_tokenizer_not_null TO passage_tokenizer_not_null;
ALTER TABLE retrieval.passage RENAME CONSTRAINT lexical_index_tsv_not_null TO passage_tsv_not_null;

-- 重建時本來就只留現行分詞器的列；萬一有並存，留任一列，下次重建會就地更正
DELETE FROM retrieval.passage AS a
 USING retrieval.passage AS b
 WHERE a.content_sha256 = b.content_sha256 AND a.tokenizer > b.tokenizer;

ALTER TABLE retrieval.passage
    DROP CONSTRAINT lexical_index_pkey,
    ADD CONSTRAINT passage_pkey PRIMARY KEY (content_sha256),
    ADD COLUMN model text CHECK (model <> ''),
    ADD COLUMN embedding vector(1024),
    ADD CONSTRAINT passage_embedded_by_model CHECK ((model IS NULL) = (embedding IS NULL));

COMMENT ON COLUMN retrieval.passage.tokenizer IS '產生 tsv 的分詞器識別碼（實作版本加詞典雜湊）；與服務端不符者不參與字面臂。';
COMMENT ON COLUMN retrieval.passage.model IS '產生 embedding 的嵌入模型識別碼；尚未嵌入為空值。';
COMMENT ON COLUMN retrieval.passage.embedding IS '段落的嵌入向量（1024 維，bge-m3）；換維度不同的模型要另一支遷移。';

CREATE INDEX passage_embedding ON retrieval.passage USING hnsw (embedding vector_cosine_ops);

DROP FUNCTION retrieval.missing_lexical(text);
DROP FUNCTION retrieval.search_articles(text, text, date, text, integer);

CREATE OR REPLACE FUNCTION retrieval.prune() RETURNS bigint
LANGUAGE sql
AS $$
    WITH gone AS (
        DELETE FROM retrieval.passage AS i
         WHERE NOT EXISTS (SELECT 1 FROM corpus.provision_version AS v
                            WHERE v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL)
        RETURNING 1
    )
    SELECT count(*) FROM gone
$$;

-- 混合檢索：兩臂各取候選池、歸到條，以名次倒數融合（k = 60，取自文獻，兩臂等權）。
-- 沒有查詢向量（嵌入取不到）時只有字面臂，排序與只用字面臂相同。
-- 語意臂先以 HNSW 取最近的段落再篩當日有效者：近似最近鄰只能先排序後過濾。
CREATE FUNCTION retrieval.search_hybrid(
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
    WITH q AS (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
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
         ORDER BY score DESC
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
         ORDER BY score DESC
         LIMIT p_pool
    ),
    fused AS (
        SELECT coalesce(l.resource_id, s.resource_id) AS resource_id,
               coalesce(l.article_path, s.article_path) AS article_path,
               l.score AS lexical, s.score AS semantic,
               coalesce(1.0 / (60 + l.r), 0) + coalesce(1.0 / (60 + s.r), 0) AS rank_score
          FROM (SELECT *, row_number() OVER (ORDER BY score DESC) AS r FROM lex) AS l
          FULL JOIN (SELECT *, row_number() OVER (ORDER BY score DESC) AS r FROM sem) AS s
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
