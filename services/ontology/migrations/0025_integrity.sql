-- 0025_integrity — 收掉重複的約束、約束名稱跟上欄名、外鍵補索引，標註層的不變式交給資料庫
--
-- - 0016 加了新約束卻沒刪掉 0001、0005 的同義舊約束：同一條規則每列檢查兩次，違反時兩個名字擇一報。
--   三欄的外鍵涵蓋兩欄的（事件連到版本時事件日必有值，由 lifecycle_event_expression_is_promulgation 保證），
--   兩欄的拿掉之後，它們唯一的被參照者 legal_expression (id, resource_id) 也拿掉。
-- - 欄位改名不會連帶改約束名（0015、0023）：錯誤訊息指向不存在的欄位。vocab.concept 的
--   concept_scheme_id_not_null1 不改：照慣例的名字與 concept_scheme 的主鍵非空約束同名。
-- - 外鍵沒有以它開頭的索引時，刪除被參照的列要整表掃描，反查「哪些陳述用了這個概念」也是。
-- - 抽取者不得驗證自己：原本只寫在 record_verification 裡，直接 INSERT 就繞過；改成觸發器。
-- - 審核狀態的每次變更都記一列 policy：原本只有 settle 記，直接 UPDATE（修正時把舊陳述改為
--   rejected）與載入時給定的 review 都不留痕跡。紀錄由觸發器寫，settle 只宣告自己是誰、依什麼政策；
--   其他呼叫端可以 SET LOCAL envcite.actor 寫明來源，否則記為 manual:<資料庫角色>。
-- - 罰則的陳述必須是罰則種類、所處罰的必須是規範性陳述；以常數生成欄加複合外鍵表達，
--   陳述改種類時同樣被擋。
-- - 概念值要件只有等於與不等於（推論只實作這兩種）。
-- - 用語定義的用語必須屬於限定在該條文所屬法規的方案：定義只在本法內使用，方案範圍就是這條規則。

-- 重複的約束

ALTER TABLE corpus.provision
    DROP CONSTRAINT provision_only_articles_are_roots,
    DROP CONSTRAINT provision_path_check;

ALTER TABLE corpus.provision_version
    DROP CONSTRAINT provision_version_interval_iff_known,
    DROP CONSTRAINT provision_version_expression_id_resource_id_fkey;

ALTER TABLE corpus.lifecycle_event
    DROP CONSTRAINT lifecycle_event_expression_only_for_promulgation,
    DROP CONSTRAINT lifecycle_event_expression_id_resource_id_fkey;

ALTER TABLE corpus.legal_expression DROP CONSTRAINT legal_expression_id_resource_id_key;

-- 約束名稱跟上欄名

ALTER TABLE annotation.derivation RENAME CONSTRAINT derivation_agent_not_null TO derivation_producer_not_null;
ALTER TABLE annotation.derivation RENAME CONSTRAINT derivation_agent_version_not_null TO derivation_producer_version_not_null;
ALTER TABLE annotation.extraction_attempt RENAME CONSTRAINT extraction_attempt_dropped_not_null TO extraction_attempt_dropped_count_not_null;
ALTER TABLE annotation.extraction_attempt RENAME CONSTRAINT extraction_attempt_statements_not_null TO extraction_attempt_statement_count_not_null;
ALTER TABLE annotation.sanction RENAME CONSTRAINT sanction_effect_not_null TO sanction_legal_effect_not_null;
ALTER TABLE annotation.sanction RENAME CONSTRAINT sanction_punishes_id_not_null TO sanction_punished_statement_id_not_null;
ALTER TABLE annotation.sanction RENAME CONSTRAINT sanction_repeating_not_null TO sanction_per_occasion_not_null;
ALTER TABLE annotation.statement RENAME CONSTRAINT statement_kind_not_null TO statement_statement_kind_not_null;
ALTER TABLE annotation.statement RENAME CONSTRAINT statement_review_not_null TO statement_confirm_state_not_null;
ALTER TABLE annotation.statement_condition RENAME CONSTRAINT statement_condition_clause_not_null TO statement_condition_disjunct_no_not_null;
ALTER TABLE annotation.statement_condition RENAME CONSTRAINT statement_condition_judgement_not_null TO statement_condition_needs_assessment_not_null;
ALTER TABLE annotation.statement_condition RENAME CONSTRAINT statement_condition_role_not_null TO statement_condition_condition_role_not_null;
ALTER TABLE annotation.statement_override RENAME CONSTRAINT statement_override_basis_not_null TO statement_override_priority_basis_not_null;
ALTER TABLE annotation.statement_override RENAME CONSTRAINT statement_override_loser_id_not_null TO statement_override_subordinate_id_not_null;
ALTER TABLE annotation.statement_override RENAME CONSTRAINT statement_override_winner_id_not_null TO statement_override_prevailing_id_not_null;
ALTER TABLE annotation.statement_override RENAME CONSTRAINT statement_override_loser_id_fkey TO statement_override_subordinate_id_fkey;
ALTER TABLE annotation.statement_override RENAME CONSTRAINT statement_override_winner_id_fkey TO statement_override_prevailing_id_fkey;
ALTER TABLE annotation.statement_slot RENAME CONSTRAINT statement_slot_slot_not_null TO statement_slot_slot_role_not_null;
ALTER TABLE annotation.term_definition RENAME CONSTRAINT term_definition_term_id_not_null TO term_definition_term_concept_id_not_null;
ALTER TABLE annotation.term_definition RENAME CONSTRAINT term_definition_term_id_fkey TO term_definition_term_concept_id_fkey;
ALTER TABLE annotation.term_definition_member RENAME CONSTRAINT term_definition_member_member_id_not_null TO term_definition_member_member_concept_id_not_null;
ALTER TABLE annotation.term_definition_member RENAME CONSTRAINT term_definition_member_member_id_fkey TO term_definition_member_member_concept_id_fkey;
ALTER TABLE corpus.legal_expression RENAME CONSTRAINT legal_expression_authority_not_null TO legal_expression_text_authority_not_null;
ALTER TABLE corpus.legal_resource RENAME CONSTRAINT legal_resource_kind_not_null TO legal_resource_norm_kind_not_null;
ALTER TABLE corpus.lifecycle_event RENAME CONSTRAINT lifecycle_event_kind_not_null TO lifecycle_event_event_kind_not_null;
ALTER TABLE corpus.provision_version RENAME CONSTRAINT provision_version_commencement_not_null TO provision_version_commencement_rule_not_null;
ALTER TABLE source.import_problem RENAME CONSTRAINT import_problem_code_not_null TO import_problem_problem_code_not_null;
ALTER TABLE source.import_problem RENAME CONSTRAINT import_problem_level_not_null TO import_problem_severity_not_null;
ALTER TABLE source.import_problem RENAME CONSTRAINT import_problem_level_check TO import_problem_severity_check;
ALTER TABLE source.snapshot RENAME CONSTRAINT snapshot_attribution_not_null TO snapshot_license_notice_not_null;
ALTER TABLE source.snapshot RENAME CONSTRAINT snapshot_attribution_check TO snapshot_license_notice_check;
ALTER TABLE source.snapshot RENAME CONSTRAINT snapshot_size_not_null TO snapshot_size_bytes_not_null;
ALTER TABLE vocab.concept RENAME CONSTRAINT concept_pref_label_not_null TO concept_name_not_null;

-- 外鍵的索引

CREATE INDEX provision_by_parent ON corpus.provision (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX lifecycle_event_by_expression ON corpus.lifecycle_event (expression_id) WHERE expression_id IS NOT NULL;
CREATE INDEX statement_by_derivation ON annotation.statement (derivation_id);
CREATE INDEX term_definition_by_derivation ON annotation.term_definition (derivation_id);
CREATE INDEX statement_condition_by_concept ON annotation.statement_condition (concept_id);
CREATE INDEX statement_condition_by_value_concept ON annotation.statement_condition (value_concept_id)
    WHERE value_concept_id IS NOT NULL;
CREATE INDEX statement_condition_by_delegate ON annotation.statement_condition (delegate_provision_id)
    WHERE delegate_provision_id IS NOT NULL;
CREATE INDEX statement_slot_by_concept ON annotation.statement_slot (concept_id) WHERE concept_id IS NOT NULL;
CREATE INDEX import_problem_by_snapshot ON source.import_problem (snapshot_id);
CREATE INDEX item_by_first_snapshot ON source.item (first_snapshot_id);
CREATE INDEX item_by_last_snapshot ON source.item (last_snapshot_id);
CREATE INDEX concept_scheme_by_scope ON vocab.concept_scheme (scope_resource_id)
    WHERE scope_resource_id IS NOT NULL;

-- 罰則與所處罰陳述的種類

ALTER TABLE annotation.statement ADD CONSTRAINT statement_id_kind UNIQUE (id, statement_kind);

ALTER TABLE annotation.sanction
    ADD COLUMN statement_kind annotation.statement_kind
        GENERATED ALWAYS AS ('sanction'::annotation.statement_kind) STORED,
    ADD COLUMN punished_statement_kind annotation.statement_kind
        GENERATED ALWAYS AS ('prescriptive'::annotation.statement_kind) STORED;

ALTER TABLE annotation.sanction
    DROP CONSTRAINT sanction_statement_id_fkey,
    DROP CONSTRAINT sanction_punishes_id_fkey,
    ADD CONSTRAINT sanction_statement_is_sanction
        FOREIGN KEY (statement_id, statement_kind)
        REFERENCES annotation.statement (id, statement_kind) ON DELETE CASCADE,
    ADD CONSTRAINT sanction_punishes_prescriptive
        FOREIGN KEY (punished_statement_id, punished_statement_kind)
        REFERENCES annotation.statement (id, statement_kind) ON DELETE CASCADE;

-- 概念值要件

ALTER TABLE annotation.statement_condition
    ADD CONSTRAINT statement_condition_value_concept_is_equality
        CHECK (value_concept_id IS NULL OR comparator IN ('eq', 'ne'));

-- 用語定義屬於本法的方案

CREATE FUNCTION annotation.term_in_own_law() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_scope    bigint;
    v_resource bigint;
BEGIN
    SELECT s.scope_resource_id INTO v_scope
      FROM vocab.concept AS k
      JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id
     WHERE k.id = NEW.term_concept_id;
    SELECT p.resource_id INTO v_resource FROM corpus.provision AS p WHERE p.id = NEW.provision_id;
    IF v_scope IS DISTINCT FROM v_resource THEN
        RAISE EXCEPTION 'term % is not in a scheme scoped to the law of provision %',
            NEW.term_concept_id, NEW.provision_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER term_definition_in_own_law
    BEFORE INSERT OR UPDATE OF term_concept_id, provision_id ON annotation.term_definition
    FOR EACH ROW EXECUTE FUNCTION annotation.term_in_own_law();

-- 抽取者不得驗證自己

CREATE FUNCTION annotation.verifier_is_not_extractor() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_producer text;
    v_what     text;
BEGIN
    IF NEW.method = 'policy' THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'verification' THEN
        v_what := 'statement';
        SELECT d.producer INTO v_producer
          FROM annotation.statement AS s
          JOIN annotation.derivation AS d ON d.id = s.derivation_id
         WHERE s.id = NEW.statement_id;
    ELSE
        v_what := 'definition';
        SELECT d.producer INTO v_producer
          FROM annotation.term_definition AS t
          JOIN annotation.derivation AS d ON d.id = t.derivation_id
         WHERE t.id = NEW.definition_id;
    END IF;
    IF NEW.verifier = v_producer THEN
        RAISE EXCEPTION 'the extractor cannot verify its own % (%)', v_what, v_producer
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER verification_not_by_extractor
    BEFORE INSERT OR UPDATE OF statement_id, method, verifier ON annotation.verification
    FOR EACH ROW EXECUTE FUNCTION annotation.verifier_is_not_extractor();

CREATE TRIGGER definition_verification_not_by_extractor
    BEFORE INSERT OR UPDATE OF definition_id, method, verifier ON annotation.definition_verification
    FOR EACH ROW EXECUTE FUNCTION annotation.verifier_is_not_extractor();

CREATE OR REPLACE FUNCTION annotation.record_verification(
    p_statement bigint,
    p_method    text,
    p_verifier  text,
    p_verdict   annotation.verdict,
    p_evidence  jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE sql
AS $$
    INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
    VALUES (p_statement, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id
$$;

CREATE OR REPLACE FUNCTION annotation.record_definition_verification(
    p_definition bigint,
    p_method     text,
    p_verifier   text,
    p_verdict    annotation.verdict,
    p_evidence   jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE sql
AS $$
    INSERT INTO annotation.definition_verification (definition_id, method, verifier, verdict, evidence)
    VALUES (p_definition, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id
$$;

-- 審核狀態的每次變更都記一列

CREATE FUNCTION annotation.log_confirm_state() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_actor    text := coalesce(nullif(current_setting('envcite.actor', true), ''),
                                'manual:' || session_user);
    v_evidence jsonb := jsonb_build_object('action', NEW.confirm_state::text)
                        || coalesce(nullif(current_setting('envcite.policy', true), '')::jsonb,
                                    '{}'::jsonb);
    v_verdict  annotation.verdict := CASE NEW.confirm_state
                                         WHEN 'confirmed' THEN 'pass'
                                         WHEN 'rejected' THEN 'fail'
                                         ELSE 'inconclusive'
                                     END;
BEGIN
    IF TG_TABLE_NAME = 'statement' THEN
        INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
        VALUES (NEW.id, 'policy', v_actor, v_verdict, v_evidence);
    ELSE
        INSERT INTO annotation.definition_verification (definition_id, method, verifier, verdict, evidence)
        VALUES (NEW.id, 'policy', v_actor, v_verdict, v_evidence);
    END IF;
    RETURN NULL;
END
$$;

CREATE TRIGGER statement_state_changed
    AFTER UPDATE OF confirm_state ON annotation.statement
    FOR EACH ROW WHEN (OLD.confirm_state IS DISTINCT FROM NEW.confirm_state)
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER statement_loaded_settled
    AFTER INSERT ON annotation.statement
    FOR EACH ROW WHEN (NEW.confirm_state <> 'extracted')
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER term_definition_state_changed
    AFTER UPDATE OF confirm_state ON annotation.term_definition
    FOR EACH ROW WHEN (OLD.confirm_state IS DISTINCT FROM NEW.confirm_state)
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER term_definition_loaded_settled
    AFTER INSERT ON annotation.term_definition
    FOR EACH ROW WHEN (NEW.confirm_state <> 'extracted')
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE OR REPLACE FUNCTION annotation.settle(p_required text[]) RETURNS TABLE(confirmed bigint, rejected bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    n_ok  bigint := 0;
    n_no  bigint := 0;
    s     record;
BEGIN
    IF p_required IS NULL OR cardinality(p_required) = 0 THEN
        RAISE EXCEPTION 'a policy needs at least one required method' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM set_config('envcite.actor', 'settle', true);
    PERFORM set_config('envcite.policy', jsonb_build_object('required', to_jsonb(p_required))::text, true);
    FOR s IN
        WITH latest AS (
            SELECT DISTINCT ON (v.statement_id, v.method) v.statement_id, v.method, v.verdict
              FROM annotation.verification AS v
             WHERE v.method <> 'policy'
             ORDER BY v.statement_id, v.method, v.id DESC
        )
        SELECT st.id,
               coalesce(bool_or(l.verdict = 'fail'), false) AS failed,
               (SELECT bool_and(EXISTS (SELECT 1 FROM latest AS x
                                         WHERE x.statement_id = st.id AND x.method = m
                                           AND x.verdict IN ('pass', 'not_applicable')))
                  FROM unnest(p_required) AS m)
               AND (SELECT bool_or(EXISTS (SELECT 1 FROM latest AS x
                                            WHERE x.statement_id = st.id AND x.method = m
                                              AND x.verdict = 'pass'))
                      FROM unnest(p_required) AS m) AS satisfied
          FROM annotation.statement AS st
          LEFT JOIN latest AS l ON l.statement_id = st.id
         WHERE st.confirm_state = 'extracted'
         GROUP BY st.id
    LOOP
        IF s.failed THEN
            UPDATE annotation.statement SET confirm_state = 'rejected' WHERE id = s.id;
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.statement SET confirm_state = 'confirmed' WHERE id = s.id;
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    PERFORM set_config('envcite.actor', '', true);
    PERFORM set_config('envcite.policy', '', true);
    RETURN QUERY SELECT n_ok, n_no;
END
$$;

CREATE OR REPLACE FUNCTION annotation.settle_definitions(p_required text[]) RETURNS TABLE(confirmed bigint, rejected bigint)
LANGUAGE plpgsql
AS $$
DECLARE
    n_ok bigint := 0;
    n_no bigint := 0;
    s    record;
BEGIN
    IF p_required IS NULL OR cardinality(p_required) = 0 THEN
        RAISE EXCEPTION 'a policy needs at least one required method' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM set_config('envcite.actor', 'settle', true);
    PERFORM set_config('envcite.policy', jsonb_build_object('required', to_jsonb(p_required))::text, true);
    FOR s IN
        WITH latest AS (
            SELECT DISTINCT ON (v.definition_id, v.method) v.definition_id, v.method, v.verdict
              FROM annotation.definition_verification AS v
             WHERE v.method <> 'policy'
             ORDER BY v.definition_id, v.method, v.id DESC
        )
        SELECT t.id,
               coalesce(bool_or(l.verdict = 'fail'), false) AS failed,
               (SELECT bool_and(EXISTS (SELECT 1 FROM latest AS x
                                         WHERE x.definition_id = t.id AND x.method = m
                                           AND x.verdict IN ('pass', 'not_applicable')))
                  FROM unnest(p_required) AS m)
               AND (SELECT bool_or(EXISTS (SELECT 1 FROM latest AS x
                                            WHERE x.definition_id = t.id AND x.method = m
                                              AND x.verdict = 'pass'))
                      FROM unnest(p_required) AS m) AS satisfied
          FROM annotation.term_definition AS t
          LEFT JOIN latest AS l ON l.definition_id = t.id
         WHERE t.confirm_state = 'extracted'
         GROUP BY t.id
    LOOP
        IF s.failed THEN
            UPDATE annotation.term_definition SET confirm_state = 'rejected' WHERE id = s.id;
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.term_definition SET confirm_state = 'confirmed' WHERE id = s.id;
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    PERFORM set_config('envcite.actor', '', true);
    PERFORM set_config('envcite.policy', '', true);
    RETURN QUERY SELECT n_ok, n_no;
END
$$;
