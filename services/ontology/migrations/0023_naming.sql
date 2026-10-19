-- 0023_naming — 欄名與型別名一致、去掉借來的詞彙、外鍵與計數寫清楚
--
-- 已套用的遷移不可改，所以這裡改名；只改名，不搬資料，約束與查詢的行為不變。
--
-- - 陳述種類 penalty 改為 sanction：罰則陳述也裝改善命令等預防性處分（legal_effect = order），
--   它們不是行政罰法第 1、2 條的處罰。種類取不利效果的總稱，與表 annotation.sanction 同名；
--   是否為處罰由 legal_effect 區分。
-- - 欄名與型別名一致：legal_value → text_authority（legal_value 是 ELI 的用語）、
--   commence_rule → commencement_rule、slot → slot_role、cond_role → condition_role，
--   型別 override_basis → priority_basis。
-- - 受控詞彙不借用 SKOS（0022）：pref_label → name；別名種類 alt、hidden → alias、non_preferred。
-- - 外鍵寫出指向什麼：term_id → term_concept_id、member_id → member_concept_id、
--   punishes_id → punished_statement_id。
-- - 計數與單位：statements、dropped → statement_count、dropped_count；size → size_bytes。
--
-- 以字串存放本體的 SQL 函式不隨改名更新（見 0015），下面依新名稱重建仍引用舊名者：
-- corpus.provisions_at（commence_rule）與 annotation.threshold_source_at（cond_role）。
-- 檢視表與 RETURN／BEGIN ATOMIC 函式存的是解析樹，自動跟著改名。

ALTER TYPE annotation.statement_kind RENAME VALUE 'penalty' TO 'sanction';

ALTER TABLE corpus.legal_expression RENAME COLUMN legal_value TO text_authority;
ALTER TABLE corpus.provision_version RENAME COLUMN commence_rule TO commencement_rule;
ALTER TABLE annotation.statement_slot RENAME COLUMN slot TO slot_role;
ALTER TYPE annotation.cond_role RENAME TO condition_role;
ALTER TABLE annotation.statement_condition RENAME COLUMN cond_role TO condition_role;
ALTER TYPE annotation.override_basis RENAME TO priority_basis;

ALTER TABLE vocab.concept RENAME COLUMN pref_label TO name;
ALTER TYPE vocab.label_kind RENAME VALUE 'alt' TO 'alias';
ALTER TYPE vocab.label_kind RENAME VALUE 'hidden' TO 'non_preferred';

ALTER TABLE annotation.term_definition RENAME COLUMN term_id TO term_concept_id;
ALTER TABLE annotation.term_definition_member RENAME COLUMN member_id TO member_concept_id;
ALTER TABLE annotation.sanction RENAME COLUMN punishes_id TO punished_statement_id;

ALTER TABLE annotation.extraction_attempt RENAME COLUMN statements TO statement_count;
ALTER TABLE annotation.extraction_attempt RENAME COLUMN dropped TO dropped_count;
ALTER TABLE source.snapshot RENAME COLUMN size TO size_bytes;

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
           SELECT v.id, e.text_status
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

CREATE OR REPLACE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
RETURNS TABLE (threshold numeric, statement_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT lim.threshold, ls.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
                                     AND ls.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.condition_role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
$$;
