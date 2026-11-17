-- 0003_suspension_status — 停止適用中的條文不是有效規範，恢復適用後回到有效
--
-- 停止與恢復適用各記生效日（effective_on）與所列的條（articles，NULL 為整部）。部分修正也記所列的條：
-- 停止適用只及於當時的條文，之後公布或發布而觸及該條的新文字依自己的施行日生效，與違憲失效不及於
-- 裁判後修正的新文字同理（corpus.invalidations_at）。
-- 停止適用列沒有生效日者不生效力：它們的範圍沒有記下，不能當成整部；由解析器重匯時補上。

ALTER TABLE corpus.lifecycle_event
    ADD COLUMN effective_on date,
    ADD COLUMN articles text[],
    ADD CONSTRAINT lifecycle_event_effective_on_is_applicability
        CHECK (effective_on IS NULL OR event_kind IN ('suspension', 'resumption')),
    ADD CONSTRAINT lifecycle_event_resumption_is_dated
        CHECK (event_kind <> 'resumption' OR effective_on IS NOT NULL),
    ADD CONSTRAINT lifecycle_event_articles_listed
        CHECK (articles IS NULL OR (
            event_kind IN ('partial_amendment', 'suspension', 'resumption')
            AND cardinality(articles) > 0
            AND array_position(articles, NULL) IS NULL
            AND array_to_string(articles, ' ') ~ '^art_[0-9]+(-[0-9]+)*( art_[0-9]+(-[0-9]+)*)*$'
        ));

COMMENT ON COLUMN corpus.lifecycle_event.effective_on IS '停止或恢復適用的生效日：原文的起日；停止適用沒寫起日者自公布或發布之日起算至第三日（中央法規標準法第 19 條第 2 項準用第 22 條第 3 項）。';
COMMENT ON COLUMN corpus.lifecycle_event.articles IS '沿革所列的條（art_N）：部分修正所及，或停止、恢復適用所及。NULL 為整部或沒有列出條號。';

CREATE FUNCTION corpus.suspended(p_resource bigint, p_article text, p_version_date date, p_on date) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT EXISTS (
        SELECT 1
          FROM corpus.lifecycle_event AS s
         WHERE s.resource_id = p_resource
           AND s.event_kind = 'suspension'
           AND s.effective_on <= p_on
           AND (s.articles IS NULL OR p_article = ANY (s.articles))
           AND NOT EXISTS (
               SELECT 1
                 FROM corpus.lifecycle_event AS e
                WHERE e.resource_id = p_resource
                  AND (e.articles IS NULL OR p_article = ANY (e.articles))
                  AND (   (e.event_kind = 'resumption'
                           AND e.effective_on >= s.effective_on AND e.effective_on <= p_on)
                       OR (e.event_kind IN ('enactment', 'full_amendment', 'partial_amendment')
                           AND e.event_date > s.event_date AND e.event_date <= p_version_date))
           )
    )
$$;

COMMENT ON FUNCTION corpus.suspended(p_resource bigint, p_article text, p_version_date date, p_on date) IS '條（p_article）在 p_on 是否停止適用中：有已生效的停止適用及於它，之後沒有已生效的恢復適用及於它，它的現行文字（版本日 p_version_date）也不是停止適用之後才公布或發布的。';

CREATE OR REPLACE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(provision_id bigint, path text, provision_type corpus.provision_type, sort_key integer[], version_id bigint, status corpus.provision_status)
    LANGUAGE sql STABLE
    AS $$
    SELECT p.id, p.path, p.provision_type, p.sort_key, cur.id,
           CASE
               WHEN cur.id IS NOT NULL
                    AND corpus.suspended(p_resource, p.article_path, cur.version_date, p_on)
                   THEN 'suspended'
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
                      AND v.commencement_rule = 'unconfirmed'
               ) THEN 'commencement_unconfirmed'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.superseded_at IS NULL
                      AND (v.force_period IS NULL OR lower(v.force_period) > p_on)
               ) THEN 'not_yet_in_force'
               ELSE 'no_longer_in_force'
           END::corpus.provision_status
      FROM corpus.provision AS p
      LEFT JOIN LATERAL (
           SELECT v.id, e.text_status, e.version_date
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND v.superseded_at IS NULL
              AND v.force_period @> p_on
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
