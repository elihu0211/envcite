-- 0035_measurement — 限值的認定方式：檢測方法與平均時段
--
-- 同一數值依即時值或平均值判斷，結論可以相反；連續自動監測（空氣污染防制法第 22 條第 1 項）與人工
-- 採樣是不同的檢測方法。兩者都是受控詞彙的概念，只屬限值角色：限值以外的要件是條文自己的文字，
-- 認定方式由它所委由的限值決定。
--
-- 推論時，限值有標而事實的認定方式不同或沒給，要件為未知；限值沒有標，照常比較，結論附一則
-- 未決事項。threshold_source_at 因此多回傳提供限值的那一列要件，推論才讀得到它的認定方式；
-- 回傳型別改了，只能先刪後建。threshold_at 只回門檻值、讀不到認定方式，推論與閘門改由同一處
-- 讀 threshold_source_at，它已沒有讀者，刪掉。
--
-- 指紋含它們，既有主張的指紋一併重算（同 0033）。

ALTER TABLE annotation.statement_condition
    ADD COLUMN measurement_method_concept_id bigint REFERENCES vocab.concept (id),
    ADD COLUMN averaging_period_concept_id bigint REFERENCES vocab.concept (id),
    ADD CONSTRAINT statement_condition_measurement_is_limit
        CHECK (condition_role = 'limit'
               OR num_nonnulls(measurement_method_concept_id, averaging_period_concept_id) = 0);

COMMENT ON COLUMN annotation.statement_condition.measurement_method_concept_id IS
    '限值的檢測方法（如連續自動監測、人工採樣）；空值是未標註。';
COMMENT ON COLUMN annotation.statement_condition.averaging_period_concept_id IS
    '限值的平均時段（如即時值、小時平均值、日平均值、最大值）；空值是未標註。';

CREATE INDEX statement_condition_by_method ON annotation.statement_condition (measurement_method_concept_id)
    WHERE measurement_method_concept_id IS NOT NULL;
CREATE INDEX statement_condition_by_averaging ON annotation.statement_condition (averaging_period_concept_id)
    WHERE averaging_period_concept_id IS NOT NULL;

DROP FUNCTION annotation.threshold_at(bigint, text, date);
DROP FUNCTION annotation.threshold_source_at(bigint, text, date);
CREATE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
 RETURNS TABLE(threshold numeric, statement_id bigint, condition_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT lim.threshold, ls.id, lim.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
      JOIN annotation.assertion AS lc ON lc.id = ls.id AND lc.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.condition_role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
$function$;

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
                               k.delegate_permit_concept_id, k.measurement_method_concept_id,
                               k.averaging_period_concept_id)
                           ORDER BY k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                                    k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                                    k.delegate_provision_id, k.needs_assessment,
                                    k.delegate_permit_concept_id, k.measurement_method_concept_id,
                                    k.averaging_period_concept_id)
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
