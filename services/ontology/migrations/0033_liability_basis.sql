-- 0033_liability_basis — 主體槽位的責任基礎
--
-- 環境法的責任不只來自行為：土壤及地下水污染整治法第 2 條第 19 款的污染土地關係人，因對土地的
-- 支配而負責（狀態責任）；環境教育法第 23 條的「有代表權之人或負責環境保護權責人員」，因代表或
-- 受指派而承擔。責任人之間的順位不另建，「污染行為人不存在時由誰負責」以例外陳述表達。
--
-- 責任基礎只屬主體槽位；沒有標註是未知，不是「行為」。
--
-- 指紋（annotation.assertion_fingerprint）要含它，否則只差責任基礎的兩個陳述會被當成同一個；
-- 算法改了，既有主張的指紋一併重算。

CREATE TYPE annotation.liability_basis AS ENUM ('act', 'state', 'representative');

COMMENT ON TYPE annotation.liability_basis IS
    'act：因自己的作為或不作為負責；state：因對物的支配負責（狀態責任）；'
    'representative：代表或受指派而承擔。';

ALTER TABLE annotation.statement_slot
    ADD COLUMN liability_basis annotation.liability_basis,
    ADD CONSTRAINT statement_slot_liability_basis_is_subject
        CHECK (liability_basis IS NULL OR slot_role = 'subject');

CREATE OR REPLACE FUNCTION annotation.assertion_fingerprint(p_assertion bigint)
 RETURNS bytea
 LANGUAGE sql
 STABLE
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
    SELECT sha256(convert_to(
        CASE c.assertion_kind
        WHEN 'statement' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(s.provision_id, encode(s.content_sha256, 'hex')),
                'kind', s.statement_kind,
                'modality', s.norm_modality,
                'slots', coalesce((
                    SELECT jsonb_agg(jsonb_build_array(sl.slot_role, sl.concept_id, sl.literal,
                                                       sl.liability_basis)
                                     ORDER BY sl.slot_role, sl.concept_id, sl.literal, sl.liability_basis)
                      FROM annotation.statement_slot AS sl WHERE sl.statement_id = s.id), '[]'),
                'conditions', coalesce((
                    SELECT jsonb_agg(jsonb_build_array(
                               k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                               k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                               k.delegate_provision_id, k.needs_assessment)
                           ORDER BY k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                                    k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                                    k.delegate_provision_id, k.needs_assessment)
                      FROM annotation.statement_condition AS k WHERE k.statement_id = s.id), '[]'),
                'sanction', (
                    SELECT jsonb_build_array(n.punished_statement_id, n.legal_effect, n.min_amount,
                                             n.max_amount, n.per_occasion, n.discretionary)
                      FROM annotation.sanction AS n WHERE n.statement_id = s.id))
              FROM annotation.statement AS s WHERE s.id = c.id)
        ELSE (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(t.provision_id, encode(t.content_sha256, 'hex')),
                'term', t.term_concept_id,
                'members', coalesce((
                    SELECT jsonb_agg(m.member_concept_id ORDER BY m.member_concept_id)
                      FROM annotation.term_definition_member AS m WHERE m.definition_id = t.id), '[]'))
              FROM annotation.term_definition AS t WHERE t.id = c.id)
        END::text, 'UTF8'))
      FROM annotation.assertion AS c
     WHERE c.id = p_assertion
$function$;

-- 指紋的算法變了：既有主張依新算法重算，同一份內容只有一種指紋，重新載入照樣沿用既有的主張。
UPDATE annotation.assertion SET fingerprint = annotation.assertion_fingerprint(id)
 WHERE fingerprint IS NOT NULL;
