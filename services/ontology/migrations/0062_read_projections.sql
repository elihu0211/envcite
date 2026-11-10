-- 0062_read_projections — 條件、槽位、罰則的欄位集合只在這裡組成一次。
--
-- 推理、gRPC 讀取與關卡各取自己要的欄、篩選與排序；函式不含 ORDER BY，呼叫端的順序才是契約。
-- 列舉欄位轉成文字，讀取端不必認得列舉型別。授權對象的管轄在法規列，不在條文列。

CREATE FUNCTION annotation.conditions_of(statement_ids bigint[])
    RETURNS TABLE (
        id bigint,
        statement_id bigint,
        condition_role text,
        disjunct_no smallint,
        concept_code text,
        concept_name text,
        comparator text,
        threshold numeric,
        measure_unit text,
        negated boolean,
        needs_assessment boolean,
        delegated boolean,
        value_code text,
        delegate_jurisdiction text,
        delegate_id_scheme text,
        delegate_local_id text,
        delegate_path text,
        permit_code text,
        method_code text,
        averaging_code text,
        carried boolean
    )
    LANGUAGE sql
    STABLE
    AS $$
        SELECT c.id, c.statement_id, c.condition_role::text, c.disjunct_no, k.code, k.name,
               c.comparator::text, c.threshold, c.measure_unit, c.negated, c.needs_assessment,
               c.delegate_provision_id IS NOT NULL, v.code,
               d.jurisdiction::text, d.id_scheme::text, d.local_id, p.path,
               permit.code, method.code, averaging.code, c.carried
          FROM annotation.statement_condition AS c
          JOIN vocab.concept AS k ON k.id = c.concept_id
          LEFT JOIN vocab.concept AS v ON v.id = c.value_concept_id
          LEFT JOIN vocab.concept AS permit ON permit.id = c.delegate_permit_concept_id
          LEFT JOIN vocab.concept AS method ON method.id = c.measurement_method_concept_id
          LEFT JOIN vocab.concept AS averaging ON averaging.id = c.averaging_period_concept_id
          LEFT JOIN corpus.provision AS p ON p.id = c.delegate_provision_id
          LEFT JOIN corpus.legal_resource AS d ON d.id = p.resource_id
         WHERE c.statement_id = ANY(statement_ids);
    $$;

COMMENT ON FUNCTION annotation.conditions_of(bigint[]) IS
    '這批陳述的條件列。不含排序；limit 是否排除由呼叫端決定。';

CREATE FUNCTION annotation.slots_of(statement_ids bigint[])
    RETURNS TABLE (
        statement_id bigint,
        slot_role text,
        alternative smallint,
        concept_code text,
        literal text,
        liability_basis text,
        unstated boolean
    )
    LANGUAGE sql
    STABLE
    AS $$
        SELECT s.statement_id, s.slot_role::text, s.alternative, k.code, s.literal,
               s.liability_basis::text, s.unstated
          FROM annotation.statement_slot AS s
          LEFT JOIN vocab.concept AS k ON k.id = s.concept_id
         WHERE s.statement_id = ANY(statement_ids);
    $$;

COMMENT ON FUNCTION annotation.slots_of(bigint[]) IS
    '這批陳述的槽位。unstated 與角色的篩選、排序由呼叫端決定。';

CREATE FUNCTION annotation.sanctions_of(statement_ids bigint[])
    RETURNS TABLE (
        statement_id bigint,
        punished_statement_id bigint,
        legal_effect text,
        amount_text text,
        min_amount numeric,
        max_amount numeric,
        per_occasion boolean,
        discretionary boolean
    )
    LANGUAGE sql
    STABLE
    AS $$
        SELECT s.statement_id, s.punished_statement_id, s.legal_effect::text, s.amount_text,
               s.min_amount, s.max_amount, s.per_occasion, s.discretionary
          FROM annotation.sanction AS s
         WHERE s.statement_id = ANY(statement_ids);
    $$;

COMMENT ON FUNCTION annotation.sanctions_of(bigint[]) IS
    '這批陳述的法律效果。是否只留罰則指向這批陳述之內，由呼叫端決定。';
