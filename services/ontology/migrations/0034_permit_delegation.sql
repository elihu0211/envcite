-- 0034_permit_delegation — 門檻值的第四種來源：委由個別處分
--
-- 限值不一定寫在條文或附表。許可證所載的排放量（空氣污染防制法第 24 條）與總量管制分配給個別
-- 場所的量（同法第 8 條至第 12 條、水污染防治法第 9 條），都是對一個場所的處分，不在任何條文單元
-- 裡。要件因此改為四者恰有其一：字面值、委由授權條文、受控值、委由個別處分。
--
-- 委由個別處分的要件只記所委由的許可種類（受控詞彙的概念）。許可的取得沒有來源，量值由呼叫端
-- 隨案件事實提供；沒有提供時，該要件為未知。限值角色仍必有字面值（0010），不受影響。
--
-- 指紋含它，既有主張的指紋一併重算（同 0033）。

ALTER TABLE annotation.statement_condition
    ADD COLUMN delegate_permit_concept_id bigint REFERENCES vocab.concept (id);

COMMENT ON COLUMN annotation.statement_condition.delegate_permit_concept_id IS
    '委由個別處分：門檻值是該種許可所載、同一概念的量值，由呼叫端隨案件事實提供。';

ALTER TABLE annotation.statement_condition
    DROP CONSTRAINT statement_condition_check,
    ADD CONSTRAINT statement_condition_one_threshold_source
        CHECK (num_nonnulls(threshold, delegate_provision_id, value_concept_id,
                            delegate_permit_concept_id) = 1);

CREATE INDEX statement_condition_by_permit ON annotation.statement_condition (delegate_permit_concept_id)
    WHERE delegate_permit_concept_id IS NOT NULL;

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
                               k.delegate_provision_id, k.needs_assessment,
                               k.delegate_permit_concept_id)
                           ORDER BY k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                                    k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                                    k.delegate_provision_id, k.needs_assessment,
                                    k.delegate_permit_concept_id)
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
