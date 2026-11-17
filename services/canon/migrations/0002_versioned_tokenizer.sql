-- 0002_versioned_tokenizer — 字面索引分版本：新分詞器建好才切換，重建期間搜尋不中斷
--
-- 分詞器識別碼由詞典（所有版本的法規標題）的雜湊決定；新增一個法規名稱就換一個識別碼，整個字面
-- 索引都要重算。原本索引與嵌入同在一列、就地改寫：重建的那段時間，索引裡新舊分詞器各占一部分，
-- 無論查詢用哪一個，都只搜得到其中一部分。
--
-- 改成搜尋引擎的藍綠索引（Elasticsearch 的 alias 切換）：
--   `retrieval.tokenizer`        每個分詞器版本與它的詞典（查詢端要用同一份詞典才分得出同樣的詞）
--   `retrieval.passage_lexical`  字面索引，每個（段落、分詞器）一列；建新版本時舊版本原封不動
--   `retrieval.serving`          目前對外服務的分詞器：新版本建完、詞頻算完才切過去，一個語句
-- 服務端只認 `serving`，輪詢它的變化，換了就換詞典；所以詞典變動不必重啟，也沒有殘缺的時段。
-- 舊版本由投影在寬限期（讓每個副本都換過去）之後清掉。
--
-- 嵌入與分詞器無關（向量只取決於文字與模型），留在 `retrieval.passage`，不隨分詞器重算。
--
-- 既有資料的承接：已算完詞頻的分詞器視為目前的詞典所建（詞典取自現行標題），其字面索引原樣搬入新表；
-- 若這個假設不成立，服務端比對識別碼不符就不換詞典，下一次投影會建出正確的版本。全新資料庫沒有任何列。

CREATE TABLE retrieval.tokenizer (
    id text NOT NULL,
    terms text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_at timestamp with time zone,
    CONSTRAINT tokenizer_id_check CHECK ((id <> ''::text)),
    CONSTRAINT tokenizer_pkey PRIMARY KEY (id)
);

COMMENT ON TABLE retrieval.tokenizer IS '分詞器版本與它的詞典：查詢端以同一份詞典分詞，才切得出建索引時的詞。retired_at 是不再服務的時間，寬限期過後才清。';

CREATE TABLE retrieval.serving (
    singleton boolean DEFAULT true NOT NULL,
    tokenizer text NOT NULL,
    CONSTRAINT serving_pkey PRIMARY KEY (singleton),
    CONSTRAINT serving_singleton_check CHECK (singleton),
    CONSTRAINT serving_tokenizer_fkey FOREIGN KEY (tokenizer) REFERENCES retrieval.tokenizer (id)
);

COMMENT ON TABLE retrieval.serving IS '目前對外服務的分詞器（只有一列）：其字面索引與詞頻都已建完；切換是對這一列的單一語句。';

CREATE TABLE retrieval.passage_lexical (
    content_sha256 bytea NOT NULL,
    tokenizer text NOT NULL,
    tsv tsvector NOT NULL,
    CONSTRAINT passage_lexical_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT passage_lexical_pkey PRIMARY KEY (content_sha256, tokenizer),
    CONSTRAINT passage_lexical_tokenizer_fkey FOREIGN KEY (tokenizer)
        REFERENCES retrieval.tokenizer (id) ON DELETE CASCADE
);

COMMENT ON TABLE retrieval.passage_lexical IS '字面索引：每個（段落、分詞器版本）一列；段落以內容雜湊為鍵，與 retrieval.passage 的嵌入共用同一個鍵。';

-- 既有資料：已算完詞頻的分詞器搬入新表。
INSERT INTO retrieval.tokenizer (id, terms)
SELECT t.tokenizer, ARRAY(SELECT DISTINCT e.title FROM corpus.legal_expression AS e WHERE e.title IS NOT NULL)
  FROM retrieval.term_stat_total AS t;

INSERT INTO retrieval.passage_lexical (content_sha256, tokenizer, tsv)
SELECT p.content_sha256, p.tokenizer, p.tsv
  FROM retrieval.passage AS p
 WHERE p.tokenizer IN (SELECT id FROM retrieval.tokenizer);

INSERT INTO retrieval.serving (tokenizer)
SELECT id FROM retrieval.tokenizer ORDER BY id LIMIT 1;

ALTER TABLE retrieval.term_stat
    ADD CONSTRAINT term_stat_tokenizer_fkey FOREIGN KEY (tokenizer)
        REFERENCES retrieval.tokenizer (id) ON DELETE CASCADE;

ALTER TABLE retrieval.term_stat_total
    ADD CONSTRAINT term_stat_total_tokenizer_fkey FOREIGN KEY (tokenizer)
        REFERENCES retrieval.tokenizer (id) ON DELETE CASCADE;

DROP INDEX retrieval.passage_tsv;

ALTER TABLE retrieval.passage DROP COLUMN tokenizer, DROP COLUMN tsv;

CREATE INDEX passage_lexical_tsv ON retrieval.passage_lexical USING gin (tsv);

COMMENT ON TABLE retrieval.passage IS '段落的嵌入向量，以內容雜湊為鍵；與分詞器無關。字面索引在 retrieval.passage_lexical。';

CREATE OR REPLACE FUNCTION retrieval.prune() RETURNS bigint
    LANGUAGE sql
    AS $$
    WITH gone_lexical AS (
        DELETE FROM retrieval.passage_lexical AS i
         WHERE NOT EXISTS (SELECT 1 FROM corpus.provision_version AS v
                            WHERE v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL)
        RETURNING 1
    ),
    gone AS (
        DELETE FROM retrieval.passage AS i
         WHERE NOT EXISTS (SELECT 1 FROM corpus.provision_version AS v
                            WHERE v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL)
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM gone) + (SELECT count(*) FROM gone_lexical)
$$;

CREATE OR REPLACE FUNCTION retrieval.refresh_term_stat(p_tokenizer text) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM retrieval.term_stat WHERE tokenizer = p_tokenizer;
    DELETE FROM retrieval.term_stat_total WHERE tokenizer = p_tokenizer;
    INSERT INTO retrieval.term_stat (tokenizer, term, doc_freq)
        SELECT p_tokenizer, s.word, s.ndoc
          FROM ts_stat(format('SELECT tsv FROM retrieval.passage_lexical WHERE tokenizer = %L', p_tokenizer)) AS s;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO retrieval.term_stat_total (tokenizer, passages)
        SELECT p_tokenizer, count(*) FROM retrieval.passage_lexical WHERE tokenizer = p_tokenizer;
    RETURN n;
END
$$;

CREATE OR REPLACE FUNCTION retrieval.search_hybrid(p_terms text, p_tokenizer text, p_embedding public.vector, p_model text, p_as_of date, p_language text, p_limit integer, p_pool integer, p_resources bigint[]) RETURNS TABLE(version_id bigint, resource_id bigint, path text, lexical_score real, semantic_similarity real, rank_score double precision)
    LANGUAGE sql STABLE
    AS $$
    WITH scope AS MATERIALIZED (SELECT p_resources AS ids),
    q AS MATERIALIZED (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    lex AS (
        SELECT p.resource_id, p.article_path, max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.passage_lexical AS i
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
