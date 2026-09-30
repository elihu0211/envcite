-- 0006_incremental — 增量更新所需的綱要
--
-- 設計見 env-law-docs 03-04：記錄時間與內容雜湊。

-- 記錄時間：更正與推導修正是新增一列並結束舊列，不覆寫（03-02 §5）。
ALTER TABLE corpus.provision_version
    ADD COLUMN recorded_at    timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN superseded_at  timestamptz,
    ADD COLUMN content_sha256 bytea,
    ADD CONSTRAINT provision_version_superseded_after_recorded
        CHECK (superseded_at IS NULL OR superseded_at > recorded_at);

-- 文字是否相同逐單元以雜湊判斷；沒有文字的單元（有下層者）以空字串計。雜湊由觸發器
-- 從內容算出，寫入端不必也不能給出與內容不符的值（生成欄位要求不可變的運算式，
-- convert_to 不是）。
CREATE FUNCTION corpus.provision_version_hash() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(coalesce(NEW.content, ''), 'UTF8'));
    RETURN NEW;
END
$$;

CREATE TRIGGER provision_version_hash
    BEFORE INSERT OR UPDATE OF content ON corpus.provision_version
    FOR EACH ROW EXECUTE FUNCTION corpus.provision_version_hash();

UPDATE corpus.provision_version SET content = content;
ALTER TABLE corpus.provision_version
    ALTER COLUMN content_sha256 SET NOT NULL,
    ADD CONSTRAINT provision_version_sha_length CHECK (octet_length(content_sha256) = 32);

-- 唯一與不重疊只約束現行列：被取代的列可以與現行列並存、相交。時態鍵不支援 WHERE，
-- 改為排除約束。
ALTER TABLE corpus.provision_version
    DROP CONSTRAINT provision_version_provision_id_expression_id_key,
    DROP CONSTRAINT provision_version_provision_id_in_force_key,
    ADD CONSTRAINT provision_version_current_no_overlap
        EXCLUDE USING gist (provision_id WITH =, in_force WITH &&) WHERE (superseded_at IS NULL);
CREATE UNIQUE INDEX provision_version_current_per_expression
    ON corpus.provision_version (provision_id, expression_id) WHERE superseded_at IS NULL;

-- 廢止之法規自廢止生效日起，各單元皆已失效，不論其施行日是否可考。
ALTER TABLE corpus.legal_resource ADD COLUMN repealed_on date;

-- 查詢只看現行列。重現過去某時點的答案，取 recorded_at 不晚於該時點、且 superseded_at
-- 為空值或晚於該時點的列。
CREATE OR REPLACE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (
    provision_id   bigint,
    path           text,
    provision_type corpus.provision_type,
    sort_key       integer[],
    version_id     bigint,
    status         corpus.provision_status
)
LANGUAGE sql STABLE
AS $$
    SELECT p.id, p.path, p.provision_type, p.sort_key, cur.id,
           CASE
               WHEN cur.id IS NOT NULL AND cur.text_status = 'unavailable' THEN 'text_unavailable'
               WHEN cur.id IS NOT NULL THEN 'in_force'
               WHEN (SELECT repealed_on FROM corpus.legal_resource WHERE id = p_resource) <= p_on
                   THEN 'no_longer_in_force'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.superseded_at IS NULL
                      AND v.commencement = 'unconfirmed'
               ) THEN 'commencement_unconfirmed'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.superseded_at IS NULL
                      AND (v.in_force IS NULL OR lower(v.in_force) > p_on)
               ) THEN 'not_yet_in_force'
               ELSE 'no_longer_in_force'
           END::corpus.provision_status
      FROM corpus.provision AS p
      LEFT JOIN LATERAL (
           SELECT v.id, e.text_status
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND v.superseded_at IS NULL
              AND v.in_force @> p_on
      ) AS cur ON true
     WHERE p.resource_id = p_resource
       AND EXISTS (
           SELECT 1
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND e.version_date <= p_on
              AND v.superseded_at IS NULL
       )
     ORDER BY p.sort_key, p.path;
$$;


-- 條的全文：由現行的下層單元組成。
CREATE OR REPLACE FUNCTION corpus.article_text(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (
        SELECT coalesce(
            v.content,
            (SELECT string_agg(cv.content, E'\n' ORDER BY c.sort_key)
               FROM corpus.provision AS c
               JOIN corpus.provision_version AS cv
                 ON cv.provision_id = c.id AND cv.expression_id = v.expression_id
              WHERE c.resource_id = p.resource_id
                AND c.path LIKE p.path || '/%'
                AND cv.superseded_at IS NULL
                AND cv.content IS NOT NULL)
        )
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id
         WHERE v.id = p_version
    );

CREATE OR REPLACE FUNCTION retrieval.search_articles(
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
    lexical_score real
)
LANGUAGE sql STABLE
AS $$
    SELECT v.id, v.resource_id, p.path, ts_rank_cd(i.content_tsv, q.query)
      FROM retrieval.article_index AS i
      JOIN corpus.provision_version AS v ON v.id = i.provision_version_id
      JOIN corpus.provision AS p ON p.id = v.provision_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     CROSS JOIN (SELECT retrieval.terms_to_tsquery(p_terms) AS query) AS q
     WHERE i.content_tsv @@ q.query
       AND i.tokenizer = p_tokenizer
       AND p.provision_type = 'article'
       AND v.superseded_at IS NULL
       AND v.in_force @> p_as_of
       AND e.language = p_language
       AND e.text_status = 'available'
     ORDER BY ts_rank_cd(i.content_tsv, q.query) DESC, v.id
     LIMIT p_limit;
$$;
