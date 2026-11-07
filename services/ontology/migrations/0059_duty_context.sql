-- 0059_duty_context — 義務的主體可以「未明示」，上位的適用情形由程式帶入並留下標記
--
-- 抽查義務確認時有兩型實質瑕疵：
--   1. 原文省略義務人時，主體被寫成被規範的設施、藥劑或行為（「飲用水設備，應符合國家標準」）。
--      主體槽位只有「某個值」，沒有「原文沒寫」可選，抽取者只好借客體。
--   2. 款的引文漏掉上位項的適用情形（「在水污染管制區內，不得有下列行為：」之下的款），
--      或後半句的引文漏掉句首的條件。
-- 兩者都不靠抽取者記得：主體由確定性程式檢查，不是原文寫出的行為人就記成「未明示」；
-- 上位或句首的適用情形由程式補成 scope 要件，並標為承自上位。
-- 本檔給資料一個放這兩種東西的位置，並讓資料庫守住它們的形狀。

-- 一、主體「未明示」

ALTER TABLE annotation.statement_slot
    ADD COLUMN unstated boolean NOT NULL DEFAULT false,
    DROP CONSTRAINT statement_slot_check,
    ADD CONSTRAINT statement_slot_has_value
        CHECK (unstated OR concept_id IS NOT NULL OR literal IS NOT NULL),
    ADD CONSTRAINT statement_slot_unstated_is_bare
        CHECK (NOT unstated
               OR (slot_role = 'subject' AND concept_id IS NULL AND literal IS NULL
                   AND liability_basis IS NULL));

COMMENT ON COLUMN annotation.statement_slot.unstated IS
    '主體槽位專用：原文沒有寫出義務人（被動式、主題句、代名詞）。沒有值，也沒有責任基礎；'
    '推論視為主體不受限制。與其他擇一的主體值互斥。';

CREATE FUNCTION annotation.subject_unstated_is_alone() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'pg_temp'
AS $$
BEGIN
    IF NEW.slot_role = 'subject' AND EXISTS (
        SELECT 1 FROM annotation.statement_slot AS o
         WHERE o.statement_id = NEW.statement_id AND o.slot_role = 'subject'
           AND o.alternative <> NEW.alternative AND (o.unstated OR NEW.unstated)
    ) THEN
        RAISE EXCEPTION 'subject of statement % is unstated, so it has no other alternative',
            NEW.statement_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER statement_slot_unstated_is_alone
    BEFORE INSERT OR UPDATE ON annotation.statement_slot
    FOR EACH ROW EXECUTE FUNCTION annotation.subject_unstated_is_alone();

-- 二、承自上位的適用情形

ALTER TABLE annotation.statement_condition
    ADD COLUMN carried boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT statement_condition_carried_is_plain_scope
        CHECK (NOT carried
               OR (condition_role = 'scope' AND comparator = 'eq' AND threshold = 1
                   AND NOT negated AND NOT needs_assessment));

COMMENT ON COLUMN annotation.statement_condition.carried IS
    '承自上位：款、目的上位項，或半句話的句首，所寫的適用情形，由程式帶入而不是抽取者寫的。'
    '只能是成立與否的 scope 要件，概念名稱是原文那一段的字。';

-- 指紋：槽位「未明示」與要件「承自上位」是內容的一部分。

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
                                                       sl.liability_basis, sl.unstated)
                                     ORDER BY sl.slot_role, sl.concept_id, sl.literal,
                                              sl.liability_basis, sl.unstated)
                      FROM annotation.statement_slot AS sl WHERE sl.statement_id = s.id), '[]'),
                'conditions', coalesce((
                    SELECT jsonb_agg(jsonb_build_array(
                               k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                               k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                               k.delegate_provision_id, k.needs_assessment,
                               k.delegate_permit_concept_id, k.measurement_method_concept_id,
                               k.averaging_period_concept_id, k.carried)
                           ORDER BY k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                                    k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                                    k.delegate_provision_id, k.needs_assessment,
                                    k.delegate_permit_concept_id, k.measurement_method_concept_id,
                                    k.averaging_period_concept_id, k.carried)
                      FROM annotation.statement_condition AS k WHERE k.statement_id = s.id), '[]'),
                'sanction', (
                    SELECT jsonb_build_array(n.punished_statement_id, n.legal_effect, n.min_amount,
                                             n.max_amount, n.per_occasion, n.discretionary)
                      FROM annotation.sanction AS n WHERE n.statement_id = s.id))
              FROM annotation.statement AS s WHERE s.id = c.id)
        WHEN 'definition' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(t.provision_id, encode(t.content_sha256, 'hex')),
                'term', t.term_concept_id,
                'members', coalesce((
                    SELECT jsonb_agg(m.member_concept_id ORDER BY m.member_concept_id)
                      FROM annotation.term_definition_member AS m WHERE m.definition_id = t.id), '[]'))
              FROM annotation.term_definition AS t WHERE t.id = c.id)
        WHEN 'delegation' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(g.command_resource_id, g.command_provision_id,
                                            encode(g.command_content_sha256, 'hex')),
                'cited', jsonb_build_array(g.cited_law_name, g.cited_path),
                'law', jsonb_build_array(g.law_resource_id, g.law_provision_id))
              FROM annotation.delegation AS g WHERE g.id = c.id)
        END::text, 'UTF8'))
      FROM annotation.assertion AS c
     WHERE c.id = p_assertion
$function$;

-- 算法變了：既有主張依新算法重算，同一份內容只有一種指紋。
UPDATE annotation.assertion SET fingerprint = annotation.assertion_fingerprint(id)
 WHERE fingerprint IS NOT NULL;
