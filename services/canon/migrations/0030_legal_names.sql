-- 0030_legal_names — 兩個在法律語境另有確定意思的名字改掉，優先依據拿掉與但書重複的值
--
-- - claim → assertion：法學上 claim 是請求權（Anspruch），也是 Hohfeld 的 claim-right；這裡的意思是
--   推導者對原文所作、待驗證的斷言（W3C PROV 與 nanopublication 稱 assertion）。表、欄、型別、
--   約束、索引、觸發器、函式與函式參數一併改名。
-- - verdict → gate_result：verdict 是判決或陪審裁決；驗證表記的只是閘門的結果。欄位
--   verification.verdict 改為 result。gate_health 檢視表輸出的欄名 verdict 不改：它是遙測指標的
--   標籤（proto/README.md），屬對外契約。
-- - priority_basis 拿掉 exception：但書是規則與例外的結構，由 statement_exception 承載，不是兩條
--   規範之間的優先依據；兩處都能表達會重複。推論不特別處理這個值，抽取不產出它。已有列用到時
--   本遷移失敗，要先把那些優先關係改寫成例外。proto 的 OVERRIDE_BASIS_EXCEPTION 不能刪（WIRE_JSON），
--   標為 deprecated，不再產生。
--
-- 以字串存放本體的函式不隨改名更新（見 0015），下面依新名稱重建；參數改名必須先刪後建，
-- 所以這幾個函式重新授權（同 0027）。

-- 改名

ALTER TYPE annotation.claim_kind RENAME TO assertion_kind;
ALTER TYPE annotation.verdict RENAME TO gate_result;
ALTER TABLE annotation.claim RENAME TO assertion;
ALTER TABLE annotation.assertion RENAME COLUMN claim_kind TO assertion_kind;
ALTER TABLE annotation.statement RENAME COLUMN claim_kind TO assertion_kind;
ALTER TABLE annotation.term_definition RENAME COLUMN claim_kind TO assertion_kind;
ALTER TABLE annotation.verification RENAME COLUMN claim_id TO assertion_id;
ALTER TABLE annotation.verification RENAME COLUMN verdict TO result;
ALTER SEQUENCE annotation.claim_id_seq RENAME TO assertion_id_seq;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_claim_kind_not_null TO assertion_assertion_kind_not_null;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_confirm_state_not_null TO assertion_confirm_state_not_null;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_derivation_id_fkey TO assertion_derivation_id_fkey;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_derivation_id_not_null TO assertion_derivation_id_not_null;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_fingerprint_check TO assertion_fingerprint_check;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_id_claim_kind_key TO assertion_id_assertion_kind_key;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_id_not_null TO assertion_id_not_null;
ALTER TABLE annotation.assertion RENAME CONSTRAINT claim_pkey TO assertion_pkey;
ALTER TABLE annotation.statement RENAME CONSTRAINT statement_is_claim TO statement_is_assertion;
ALTER TABLE annotation.term_definition RENAME CONSTRAINT term_definition_is_claim TO term_definition_is_assertion;
ALTER TABLE annotation.verification RENAME CONSTRAINT verification_claim_id_fkey TO verification_assertion_id_fkey;
ALTER TABLE annotation.verification RENAME CONSTRAINT verification_claim_id_not_null TO verification_assertion_id_not_null;
ALTER TABLE annotation.verification RENAME CONSTRAINT verification_verdict_not_null TO verification_result_not_null;
ALTER INDEX annotation.claim_by_derivation RENAME TO assertion_by_derivation;
ALTER INDEX annotation.claim_extracted RENAME TO assertion_extracted;
ALTER INDEX annotation.claim_unique_content RENAME TO assertion_unique_content;
ALTER INDEX annotation.verification_by_claim RENAME TO verification_by_assertion;
ALTER TRIGGER claim_loaded_settled ON annotation.assertion RENAME TO assertion_loaded_settled;
ALTER TRIGGER claim_loaded_settled_by_reviewer ON annotation.assertion RENAME TO assertion_loaded_settled_by_reviewer;
ALTER TRIGGER claim_sealed ON annotation.assertion RENAME TO assertion_sealed;
ALTER TRIGGER claim_state_changed ON annotation.assertion RENAME TO assertion_state_changed;
ALTER TRIGGER statement_drops_claim ON annotation.statement RENAME TO statement_drops_assertion;
ALTER TRIGGER term_definition_drops_claim ON annotation.term_definition RENAME TO term_definition_drops_assertion;
ALTER FUNCTION annotation.claim_sealed_at_commit() RENAME TO assertion_sealed_at_commit;
ALTER FUNCTION annotation.drop_claim() RENAME TO drop_assertion;

-- 依新名稱重建函式本體（參數改名者先刪後建）

DROP FUNCTION annotation.claim_fingerprint(bigint);
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
                    SELECT jsonb_agg(jsonb_build_array(sl.slot_role, sl.concept_id, sl.literal)
                                     ORDER BY sl.slot_role, sl.concept_id, sl.literal)
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

CREATE OR REPLACE FUNCTION annotation.assertion_sealed_at_commit()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.assertion WHERE id = NEW.id AND fingerprint IS NULL) THEN
        RAISE EXCEPTION 'assertion % was written but not sealed', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.content_after_seal()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    -- 觸發器參數是指向主張的欄名：各表不同，NEW 上不存在的欄位不能寫進同一個運算式
    v_assertion bigint := (to_jsonb(NEW) ->> TG_ARGV[0])::bigint;
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.assertion WHERE id = v_assertion AND fingerprint IS NOT NULL) THEN
        RAISE EXCEPTION 'assertion % is sealed; its content cannot change', v_assertion
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.definitions_at(p_resource bigint, p_language text, p_on date)
 RETURNS TABLE(definition_id bigint, version_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT t.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.term_definition AS t ON t.provision_id = v.provision_id
                                          AND t.content_sha256 = v.content_sha256
      JOIN annotation.assertion AS c ON c.id = t.id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE a.status = 'in_force'
       AND c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
$function$;

CREATE OR REPLACE FUNCTION annotation.drop_assertion()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
BEGIN
    DELETE FROM annotation.assertion WHERE id = OLD.id;
    RETURN NULL;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.loaded_settled_needs_reviewer()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.confirm_state <> 'extracted'
       AND NOT pg_has_role(current_user, 'envcite_ontology_reviewer', 'MEMBER') THEN
        RAISE EXCEPTION 'only a reviewer can load an assertion as %', NEW.confirm_state
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.log_confirm_state()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.verification AS v
                WHERE v.assertion_id = NEW.id AND v.method = 'policy'
                  AND v.recorded_in = pg_current_xact_id()
                  AND v.evidence->>'action' = NEW.confirm_state::text) THEN
        RETURN NULL;
    END IF;
    INSERT INTO annotation.verification (assertion_id, method, verifier, result, evidence)
    VALUES (
        NEW.id,
        'policy',
        CASE TG_OP WHEN 'INSERT' THEN 'loaded' ELSE 'direct' END,
        CASE NEW.confirm_state WHEN 'confirmed' THEN 'pass' WHEN 'rejected' THEN 'fail'
                               ELSE 'inconclusive' END::annotation.gate_result,
        jsonb_build_object('action', NEW.confirm_state::text)
    );
    RETURN NULL;
END
$function$;

DROP FUNCTION annotation.record_verification(bigint,text,text,annotation.gate_result,jsonb);
CREATE OR REPLACE FUNCTION annotation.record_verification(p_assertion bigint, p_method text, p_verifier text, p_result annotation.gate_result, p_evidence jsonb DEFAULT '{}'::jsonb)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
DECLARE
    v_id bigint;
BEGIN
    IF p_method = 'policy' THEN
        RAISE EXCEPTION 'policy records are written by settle and set_confirm_state'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO annotation.verification (assertion_id, method, verifier, result, evidence)
    VALUES (p_assertion, p_method, p_verifier, p_result, p_evidence)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$function$;

DROP FUNCTION annotation.seal(bigint);
CREATE OR REPLACE FUNCTION annotation.seal(p_assertion bigint)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
DECLARE
    v_kind     annotation.assertion_kind;
    v_sealed   bytea;
    v_print    bytea;
    v_existing bigint;
BEGIN
    SELECT assertion_kind, fingerprint INTO v_kind, v_sealed FROM annotation.assertion WHERE id = p_assertion;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such assertion: %', p_assertion USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_sealed IS NOT NULL THEN
        RAISE EXCEPTION 'assertion % is already sealed', p_assertion USING ERRCODE = 'check_violation';
    END IF;
    v_print := annotation.assertion_fingerprint(p_assertion);
    IF v_print IS NULL THEN
        RAISE EXCEPTION 'assertion % has no subtype row to seal', p_assertion USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO v_existing FROM annotation.assertion
     WHERE assertion_kind = v_kind AND fingerprint = v_print AND confirm_state <> 'rejected';
    IF FOUND THEN
        DELETE FROM annotation.assertion WHERE id = p_assertion;
        RETURN v_existing;
    END IF;
    UPDATE annotation.assertion SET fingerprint = v_print WHERE id = p_assertion;
    RETURN p_assertion;
END
$function$;

DROP FUNCTION annotation.set_confirm_state(bigint,annotation.confirm_state,text);
CREATE OR REPLACE FUNCTION annotation.set_confirm_state(p_assertion bigint, p_state annotation.confirm_state, p_reason text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
BEGIN
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'a manual review needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM annotation.assertion WHERE id = p_assertion) THEN
        RAISE EXCEPTION 'no such assertion: %', p_assertion USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO annotation.verification (assertion_id, method, verifier, result, evidence)
    VALUES (p_assertion, 'policy', 'review',
            CASE p_state WHEN 'confirmed' THEN 'pass' WHEN 'rejected' THEN 'fail'
                         ELSE 'inconclusive' END::annotation.gate_result,
            jsonb_build_object('action', p_state::text, 'reason', p_reason));
    UPDATE annotation.assertion SET confirm_state = p_state WHERE id = p_assertion;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.settle(p_kind annotation.assertion_kind, p_required text[])
 RETURNS TABLE(confirmed bigint, rejected bigint)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
DECLARE
    n_ok bigint := 0;
    n_no bigint := 0;
    s    record;
BEGIN
    IF p_required IS NULL OR cardinality(p_required) = 0 THEN
        RAISE EXCEPTION 'a policy needs at least one required method' USING ERRCODE = 'check_violation';
    END IF;
    FOR s IN
        WITH latest AS (
            SELECT DISTINCT ON (v.assertion_id, v.method) v.assertion_id, v.method, v.result
              FROM annotation.verification AS v
              JOIN annotation.assertion AS c ON c.id = v.assertion_id
             WHERE v.method <> 'policy' AND c.assertion_kind = p_kind AND c.confirm_state = 'extracted'
             ORDER BY v.assertion_id, v.method, v.id DESC
        )
        SELECT c.id,
               coalesce(bool_or(l.result = 'fail'), false) AS failed,
               (SELECT bool_and(EXISTS (SELECT 1 FROM latest AS x
                                         WHERE x.assertion_id = c.id AND x.method = m
                                           AND x.result IN ('pass', 'not_applicable')))
                  FROM unnest(p_required) AS m)
               AND (SELECT bool_or(EXISTS (SELECT 1 FROM latest AS x
                                            WHERE x.assertion_id = c.id AND x.method = m
                                              AND x.result = 'pass'))
                      FROM unnest(p_required) AS m) AS satisfied
          FROM annotation.assertion AS c
          LEFT JOIN latest AS l ON l.assertion_id = c.id
         WHERE c.assertion_kind = p_kind AND c.confirm_state = 'extracted'
         GROUP BY c.id
    LOOP
        IF s.failed THEN
            INSERT INTO annotation.verification (assertion_id, method, verifier, result, evidence)
            VALUES (s.id, 'policy', 'settle', 'fail',
                    jsonb_build_object('action', 'rejected', 'required', to_jsonb(p_required)));
            UPDATE annotation.assertion SET confirm_state = 'rejected' WHERE id = s.id;
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            INSERT INTO annotation.verification (assertion_id, method, verifier, result, evidence)
            VALUES (s.id, 'policy', 'settle', 'pass',
                    jsonb_build_object('action', 'confirmed', 'required', to_jsonb(p_required)));
            UPDATE annotation.assertion SET confirm_state = 'confirmed' WHERE id = s.id;
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT n_ok, n_no;
END
$function$;

CREATE OR REPLACE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
 RETURNS TABLE(statement_id bigint, version_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.assertion AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
$function$;

CREATE OR REPLACE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
 RETURNS TABLE(threshold numeric, statement_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT lim.threshold, ls.id
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

CREATE OR REPLACE FUNCTION annotation.verifier_is_not_extractor()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_producer text;
BEGIN
    IF NEW.method = 'policy' THEN
        RETURN NEW;
    END IF;
    SELECT d.producer INTO v_producer
      FROM annotation.assertion AS c
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.id = NEW.assertion_id;
    IF NEW.verifier = v_producer THEN
        RAISE EXCEPTION 'the extractor cannot verify its own assertion (%)', v_producer
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$function$;

-- 先刪後建的函式重新授權（0027、0028）
REVOKE EXECUTE ON FUNCTION annotation.record_verification(bigint,text,text,annotation.gate_result,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION annotation.record_verification(bigint,text,text,annotation.gate_result,jsonb) TO envcite_ontology_writer, envcite_ontology_reviewer;
REVOKE EXECUTE ON FUNCTION annotation.seal(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION annotation.seal(bigint) TO envcite_ontology_writer, envcite_ontology_reviewer;
REVOKE EXECUTE ON FUNCTION annotation.set_confirm_state(bigint,annotation.confirm_state,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION annotation.set_confirm_state(bigint,annotation.confirm_state,text) TO envcite_ontology_reviewer;

-- 優先依據拿掉 exception

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.statement_override WHERE priority_basis = 'exception') THEN
        RAISE EXCEPTION 'statement_override rows use priority_basis exception; rewrite them as statement_exception first'
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE TYPE annotation.priority_basis_0030 AS ENUM ('hierarchy', 'lex_specialis', 'lex_posterior');
ALTER TABLE annotation.statement_override
    ALTER COLUMN priority_basis TYPE annotation.priority_basis_0030
    USING priority_basis::text::annotation.priority_basis_0030;
DROP TYPE annotation.priority_basis;
ALTER TYPE annotation.priority_basis_0030 RENAME TO priority_basis;

COMMENT ON TYPE annotation.priority_basis IS 'hierarchy：中央法規標準法第 11 條；lex_specialis：同法第 16 條；lex_posterior：學理（同法第 21 條第 4 款是廢止，不是優先），只在有明文標註時採用。但書不是優先依據，記在 statement_exception。';
