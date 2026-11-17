-- 0001_baseline — canon 的完整綱要：由此起套用，之前的 71 個遷移檔已合併於此
--
-- 以 pg_dump --schema-only 自「套完舊遷移、再刪掉沒有讀寫者的欄位」的資料庫產生，不手改內容；
-- 每個設計決策的理由在 COMMENT ON 與 git 歷史（合併前的 migrations/*.sql）。之後的綱要變更一律
-- 新增遷移檔，本檔與其他已套用的檔一樣由 schema_migration 的雜湊保護。
--
-- 角色由部署庫的 CloudNativePG managed roles 建立；本機與測試的資料庫沒有時才在這裡建（擁有者
-- 沒有建角色的權限時不會走到 CREATE ROLE）。

SET LOCAL check_function_bodies = false;
SET LOCAL client_min_messages = warning;
SELECT pg_catalog.set_config('search_path', '', true);

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['canon_reader', 'canon_writer', 'canon_reviewer'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE pg_catalog.format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END
$$;

CREATE SCHEMA annotation;

CREATE SCHEMA corpus;

CREATE SCHEMA guidance;

CREATE SCHEMA judgment;

CREATE SCHEMA retrieval;

CREATE SCHEMA scoring;

CREATE SCHEMA source;

CREATE SCHEMA vocab;

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TYPE annotation.assertion_kind AS ENUM (
    'statement',
    'definition',
    'delegation'
);

CREATE TYPE annotation.comparator AS ENUM (
    'le',
    'lt',
    'ge',
    'gt',
    'eq',
    'ne'
);

CREATE TYPE annotation.condition_role AS ENUM (
    'scope',
    'duty',
    'limit'
);

CREATE TYPE annotation.confirm_state AS ENUM (
    'extracted',
    'confirmed',
    'rejected'
);

CREATE TYPE annotation.derivation_method AS ENUM (
    'rule',
    'llm',
    'human'
);

CREATE TYPE annotation.gate_result AS ENUM (
    'pass',
    'fail',
    'inconclusive',
    'not_applicable'
);

CREATE TYPE annotation.legal_effect AS ENUM (
    'fine',
    'confiscation',
    'restriction',
    'deprivation',
    'reputation',
    'warning',
    'order',
    'disgorgement'
);

CREATE TYPE annotation.liability_basis AS ENUM (
    'act',
    'state',
    'representative'
);

COMMENT ON TYPE annotation.liability_basis IS 'act：因自己的作為或不作為負責；state：因對物的支配負責（狀態責任）；representative：代表或受指派而承擔。';

CREATE TYPE annotation.norm_modality AS ENUM (
    'obligation',
    'prohibition',
    'permission'
);

CREATE TYPE annotation.priority_basis AS ENUM (
    'hierarchy',
    'lex_specialis',
    'lex_posterior',
    'mutatis_mutandis',
    'subsidiarity',
    'absorption'
);

COMMENT ON TYPE annotation.priority_basis IS 'hierarchy：中央法規標準法第 11 條；lex_specialis：同法第 16 條；lex_posterior：學理（同法第 21 條第 4 款是廢止，不是優先），只在有明文標註時採用。但書不是優先依據，記在 statement_exception。';

CREATE TYPE annotation.slot_role AS ENUM (
    'subject',
    'action',
    'object',
    'medium',
    'time',
    'place'
);

CREATE TYPE annotation.statement_kind AS ENUM (
    'prescriptive',
    'constitutive',
    'sanction',
    'reparation'
);

CREATE TYPE annotation.trigger_kind AS ENUM (
    'order_lapsed',
    'sanction_imposed'
);

COMMENT ON TYPE annotation.trigger_kind IS 'order_lapsed：命令屆期未履行；sanction_imposed：另一處分已作成。';

CREATE TYPE annotation.violation_kind AS ENUM (
    'duty',
    'delegated_rule'
);

CREATE TYPE corpus.commencement_rule AS ENUM (
    'unchanged',
    'promulgation',
    'specified',
    'by_order',
    'unconfirmed',
    'retroactive'
);

CREATE TYPE corpus.event_kind AS ENUM (
    'enactment',
    'full_amendment',
    'partial_amendment',
    'commencement',
    'repeal',
    'sunset',
    'suspension',
    'unconstitutional',
    'correction',
    'unknown'
);

CREATE TYPE corpus.heading_kind AS ENUM (
    'part',
    'chapter',
    'section',
    'subsection',
    'item_heading'
);

CREATE TYPE corpus.id_scheme AS ENUM (
    'moj_pcode',
    'local_glrs'
);

CREATE TYPE corpus.jurisdiction AS ENUM (
    'TW-CENTRAL',
    'TW-TPE',
    'TW-NWT',
    'TW-TAO',
    'TW-TXG',
    'TW-TNN',
    'TW-KHH',
    'TW-KEE',
    'TW-HSZ',
    'TW-CYI',
    'TW-HSQ',
    'TW-MIA',
    'TW-CHA',
    'TW-NAN',
    'TW-YUN',
    'TW-CYQ',
    'TW-PIF',
    'TW-ILA',
    'TW-HUA',
    'TW-TTT',
    'TW-PEN',
    'TW-KIN',
    'TW-LIE'
);

COMMENT ON TYPE corpus.jurisdiction IS '法規的管轄：TW-CENTRAL 為中央，其餘為 ISO 3166-2:TW 的 22 個縣市。';

CREATE TYPE corpus.norm_kind AS ENUM (
    'constitution',
    'statute',
    'regulation',
    'local_ordinance',
    'local_regulation'
);

CREATE TYPE corpus.provision_status AS ENUM (
    'in_force',
    'text_unavailable',
    'not_yet_in_force',
    'no_longer_in_force',
    'commencement_unconfirmed'
);

CREATE TYPE corpus.provision_type AS ENUM (
    'article',
    'paragraph',
    'subparagraph',
    'item',
    'subitem'
);

CREATE TYPE corpus.ruling_kind AS ENUM (
    'interpretation',
    'judgment'
);

CREATE TYPE corpus.text_authority AS ENUM (
    'gazette',
    'competent_authority',
    'official_db',
    'unverified',
    'legislature'
);

CREATE TYPE corpus.text_status AS ENUM (
    'available',
    'unavailable'
);

CREATE TYPE guidance.cessation_cause AS ENUM (
    'successor',
    'announced',
    'taken_down'
);

CREATE TYPE guidance.rule_kind AS ENUM (
    'interpretation',
    'discretion_standard',
    'operational'
);

COMMENT ON TYPE guidance.rule_kind IS 'interpretation、discretion_standard：行政程序法第 159 條第 2 項第 2 款（解釋性規定、裁量基準）；operational：同項第 1 款（組織性、作業性規定）。';

CREATE TYPE judgment.fetch_status AS ENUM (
    'pending',
    'stored',
    'file_only',
    'taken_down',
    'unmapped'
);

COMMENT ON TYPE judgment.fetch_status IS 'pending：尚未取完，下一夜再試；stored：全文已入庫；file_only：全文是檔案網址，不下載；taken_down：來源回應查無資料；unmapped：行政法院代碼不在審級表，不重試。';

CREATE TYPE judgment.instance AS ENUM (
    'supreme',
    'high',
    'district'
);

COMMENT ON TYPE judgment.instance IS '審級：最高行政法院、高等行政法院、地方行政法院。';

CREATE TYPE judgment.kind AS ENUM (
    'ordinary',
    'grand_chamber'
);

COMMENT ON TYPE judgment.kind IS 'ordinary：一般判決與裁定；grand_chamber：最高行政法院大法庭裁定（只拘束提案庭於該案）。';

CREATE TYPE judgment.version_basis AS ENUM (
    'stated',
    'decided_on'
);

COMMENT ON TYPE judgment.version_basis IS 'stated：裁判理由載明行為時、裁處時或修正前等版本，並對應到唯一版本；decided_on：理由未載明，以裁判日現行的版本錨定。';

CREATE TYPE scoring.factor_role AS ENUM (
    'base_point',
    'aggravation_point',
    'mitigation_point',
    'multiplicative_factor',
    'additive_rate',
    'demerit_point',
    'fixed_amount'
);

CREATE TYPE scoring.formula_kind AS ENUM (
    'additive_points_times_base',
    'multiplicative_factors',
    'demerit_accumulation',
    'fixed_schedule'
);

CREATE TYPE source.feed AS ENUM (
    'moj_law',
    'moj_order',
    'gazette',
    'moenv_guidance',
    'moenv_law',
    'ly_law'
);

CREATE TYPE vocab.concept_relation_kind AS ENUM (
    'broader',
    'exact_match'
);

CREATE TYPE vocab.label_kind AS ENUM (
    'alias',
    'non_preferred'
);

CREATE FUNCTION annotation.anchored_statements_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(statement_id bigint, version_id bigint)
    LANGUAGE sql STABLE
    AS $$
    SELECT s.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS s ON s.provision_id = v.provision_id
                                    AND s.content_sha256 = v.content_sha256
     WHERE a.status = 'in_force'
$$;

CREATE FUNCTION annotation.assertion_fingerprint(p_assertion bigint) RETURNS bytea
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
$$;

CREATE FUNCTION annotation.assertion_sealed_at_commit() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.assertion WHERE id = NEW.id AND fingerprint IS NULL) THEN
        RAISE EXCEPTION 'assertion % was written but not sealed', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;

CREATE FUNCTION annotation.conditions_of(statement_ids bigint[]) RETURNS TABLE(id bigint, statement_id bigint, condition_role text, disjunct_no smallint, concept_code text, concept_name text, comparator text, threshold numeric, measure_unit text, negated boolean, needs_assessment boolean, delegated boolean, value_code text, delegate_jurisdiction text, delegate_id_scheme text, delegate_local_id text, delegate_path text, permit_code text, method_code text, averaging_code text, carried boolean)
    LANGUAGE sql STABLE
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

COMMENT ON FUNCTION annotation.conditions_of(statement_ids bigint[]) IS '這批陳述的條件列。不含排序；limit 是否排除由呼叫端決定。';

CREATE FUNCTION annotation.content_after_seal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION annotation.definitions_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(definition_id bigint, version_id bigint)
    LANGUAGE sql STABLE
    AS $$
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
$$;

CREATE FUNCTION annotation.delegated_by(p_law bigint, p_language text, p_on date) RETURNS TABLE(delegation_id bigint, command_resource_id bigint, command_local_id text, command_provision_id bigint, law_provision_id bigint, law_version_id bigint, law_status corpus.provision_status)
    LANGUAGE sql STABLE
    AS $$
    SELECT g.id, g.command_resource_id, cr.local_id, g.command_provision_id,
           g.law_provision_id, a.version_id, a.status
      FROM annotation.delegation_usable AS g
      JOIN corpus.legal_resource AS cr ON cr.id = g.command_resource_id
      LEFT JOIN corpus.provisions_at(p_law, p_language, p_on) AS a
           ON a.provision_id = g.law_provision_id
     WHERE g.law_resource_id = p_law
       AND annotation.delegation_command_in_force(
                g.command_resource_id, g.command_provision_id, g.command_content_sha256, p_language, p_on)
     ORDER BY cr.local_id, g.id
$$;

CREATE FUNCTION annotation.delegation_command_in_force(p_resource bigint, p_provision bigint, p_sha256 bytea, p_language text, p_on date) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE WHEN p_provision IS NULL THEN
        EXISTS (SELECT 1 FROM corpus.provision_version AS v
                 WHERE v.resource_id = p_resource
                   AND v.language = p_language AND v.superseded_at IS NULL AND v.force_period @> p_on)
    ELSE
        EXISTS (SELECT 1 FROM corpus.provision_version AS v
                 WHERE v.provision_id = p_provision
                   AND v.language = p_language AND v.superseded_at IS NULL AND v.force_period @> p_on
                   AND v.content_sha256 = p_sha256)
    END
$$;

CREATE FUNCTION annotation.delegations_at(p_command bigint, p_language text, p_on date) RETURNS TABLE(delegation_id bigint, command_provision_id bigint, cited_law_name text, cited_path text, law_resource_id bigint, law_local_id text, law_provision_id bigint, law_version_id bigint, law_status corpus.provision_status)
    LANGUAGE sql STABLE
    AS $$
    SELECT g.id, g.command_provision_id, g.cited_law_name, g.cited_path,
           g.law_resource_id, lr.local_id, g.law_provision_id, a.version_id, a.status
      FROM annotation.delegation_usable AS g
      LEFT JOIN corpus.legal_resource AS lr ON lr.id = g.law_resource_id
      LEFT JOIN LATERAL corpus.provisions_at(g.law_resource_id, p_language, p_on) AS a
           ON a.provision_id = g.law_provision_id
     WHERE g.command_resource_id = p_command
       AND annotation.delegation_command_in_force(
                g.command_resource_id, g.command_provision_id, g.command_content_sha256, p_language, p_on)
     ORDER BY g.cited_path, g.id
$$;

CREATE FUNCTION annotation.drop_assertion() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
BEGIN
    DELETE FROM annotation.assertion WHERE id = OLD.id;
    RETURN NULL;
END
$$;

CREATE FUNCTION annotation.invalidated_statements_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(statement_id bigint, version_id bigint, ruling_no text, invalid_from date, partial boolean)
    LANGUAGE sql STABLE
    AS $$
    SELECT r.statement_id, r.version_id, i.ruling_no, i.invalid_from, ri.partial
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.assertion AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
      JOIN corpus.invalidations_at(p_resource, p_language, p_on) AS i ON i.version_id = r.version_id
      JOIN corpus.ruling_invalidation AS ri ON ri.ruling_id = i.ruling_id
                                           AND ri.provision_id = i.provision_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
     ORDER BY r.statement_id, i.invalid_from, i.ruling_no
$$;

CREATE FUNCTION annotation.loaded_settled_needs_reviewer() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.confirm_state <> 'extracted'
       AND NOT pg_has_role(current_user, 'canon_reviewer', 'MEMBER') THEN
        RAISE EXCEPTION 'only a reviewer can load an assertion as %', NEW.confirm_state
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION annotation.log_confirm_state() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
$$;

CREATE FUNCTION annotation.penalty_candidates(p_held bigint) RETURNS TABLE(statement_id bigint, path text, confirmed boolean)
    LANGUAGE sql STABLE
    AS $$
    SELECT s.id, p.path, a.confirm_state = 'confirmed'
      FROM annotation.held_penalty AS h
      JOIN annotation.statement AS s ON s.id = h.punished_statement_id
      JOIN annotation.assertion AS a ON a.id = s.id
      JOIN corpus.provision AS p ON p.id = s.provision_id
     WHERE h.id = p_held AND a.confirm_state <> 'rejected'
    UNION
    SELECT s.id, p.path, a.confirm_state = 'confirmed'
      FROM annotation.held_penalty AS h
      JOIN annotation.current_violation AS vi
        ON vi.provision_id = h.provision_id AND vi.content_sha256 = h.content_sha256
      JOIN corpus.provision AS c ON c.id = vi.cited_provision_id
      JOIN corpus.provision AS p
        ON p.resource_id = c.resource_id
       AND (p.path = c.path OR p.path LIKE c.path || '/%' OR c.path LIKE p.path || '/%')
      JOIN annotation.statement AS s ON s.provision_id = p.id
      JOIN corpus.provision_version AS v
        ON v.provision_id = s.provision_id AND v.content_sha256 = s.content_sha256
       AND v.superseded_at IS NULL
      JOIN annotation.assertion AS a ON a.id = s.id
     WHERE h.id = p_held AND h.punished_statement_id IS NULL AND vi.violation_kind = 'duty'
       AND s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')
       AND a.confirm_state <> 'rejected'
$$;

CREATE FUNCTION annotation.record_verification(p_assertion bigint, p_method text, p_verifier text, p_result annotation.gate_result, p_evidence jsonb DEFAULT '{}'::jsonb) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
$$;

CREATE FUNCTION annotation.sanction_punishes_confirmed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM annotation.assertion
                    WHERE id = NEW.punished_statement_id AND confirm_state = 'confirmed') THEN
        RAISE EXCEPTION 'statement % punishes statement %, which is not confirmed',
            NEW.statement_id, NEW.punished_statement_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION annotation.sanctions_of(statement_ids bigint[]) RETURNS TABLE(statement_id bigint, punished_statement_id bigint, legal_effect text, amount_text text, min_amount numeric, max_amount numeric, per_occasion boolean, discretionary boolean)
    LANGUAGE sql STABLE
    AS $$
        SELECT s.statement_id, s.punished_statement_id, s.legal_effect::text, s.amount_text,
               s.min_amount, s.max_amount, s.per_occasion, s.discretionary
          FROM annotation.sanction AS s
         WHERE s.statement_id = ANY(statement_ids);
    $$;

COMMENT ON FUNCTION annotation.sanctions_of(statement_ids bigint[]) IS '這批陳述的法律效果。是否只留罰則指向這批陳述之內，由呼叫端決定。';

CREATE FUNCTION annotation.scope_keeps_definitions() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM annotation.term_definition AS t
          JOIN vocab.concept AS k ON k.id = t.term_concept_id
          JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id
          JOIN corpus.provision AS p ON p.id = t.provision_id
         WHERE (CASE TG_TABLE_NAME WHEN 'concept' THEN k.id ELSE s.id END) = NEW.id
           AND s.scope_resource_id IS DISTINCT FROM p.resource_id
    ) THEN
        RAISE EXCEPTION 'a defined term would leave the scheme scoped to its law'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;

CREATE FUNCTION annotation.seal(p_assertion bigint) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
$$;

CREATE FUNCTION annotation.set_confirm_state(p_assertion bigint, p_state annotation.confirm_state, p_reason text) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
$$;

CREATE FUNCTION annotation.settle(p_kind annotation.assertion_kind, p_required text[]) RETURNS TABLE(confirmed bigint, rejected bigint)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'pg_temp'
    AS $$
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
        ),
        punished AS (
            SELECT n.statement_id, t.confirm_state
              FROM annotation.sanction AS n
              JOIN annotation.assertion AS t ON t.id = n.punished_statement_id
        )
        SELECT c.id,
               coalesce(bool_or(l.result = 'fail'), false)
                   OR coalesce((SELECT p.confirm_state = 'rejected' FROM punished AS p
                                 WHERE p.statement_id = c.id), false) AS failed,
               (SELECT bool_and(EXISTS (SELECT 1 FROM latest AS x
                                         WHERE x.assertion_id = c.id AND x.method = m
                                           AND x.result IN ('pass', 'not_applicable')))
                  FROM unnest(p_required) AS m)
               AND (SELECT bool_or(EXISTS (SELECT 1 FROM latest AS x
                                            WHERE x.assertion_id = c.id AND x.method = m
                                              AND x.result = 'pass'))
                      FROM unnest(p_required) AS m)
               AND coalesce((SELECT p.confirm_state = 'confirmed' FROM punished AS p
                              WHERE p.statement_id = c.id), true) AS satisfied
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
$$;

CREATE FUNCTION annotation.slots_of(statement_ids bigint[]) RETURNS TABLE(statement_id bigint, slot_role text, alternative smallint, concept_code text, literal text, liability_basis text, unstated boolean)
    LANGUAGE sql STABLE
    AS $$
        SELECT s.statement_id, s.slot_role::text, s.alternative, k.code, s.literal,
               s.liability_basis::text, s.unstated
          FROM annotation.statement_slot AS s
          LEFT JOIN vocab.concept AS k ON k.id = s.concept_id
         WHERE s.statement_id = ANY(statement_ids);
    $$;

COMMENT ON FUNCTION annotation.slots_of(statement_ids bigint[]) IS '這批陳述的槽位。unstated 與角色的篩選、排序由呼叫端決定。';

CREATE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(statement_id bigint, version_id bigint)
    LANGUAGE sql STABLE
    AS $$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.assertion AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
       AND NOT EXISTS (SELECT 1 FROM corpus.invalidations_at(p_resource, p_language, p_on) AS i
                        WHERE i.version_id = r.version_id)
$$;

CREATE TABLE annotation.statement (
    id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    span int4range,
    statement_kind annotation.statement_kind NOT NULL,
    norm_modality annotation.norm_modality,
    assertion_kind annotation.assertion_kind GENERATED ALWAYS AS ('statement'::annotation.assertion_kind) STORED,
    CONSTRAINT statement_check CHECK (((statement_kind = 'prescriptive'::annotation.statement_kind) = (norm_modality IS NOT NULL))),
    CONSTRAINT statement_content_sha256_check CHECK ((octet_length(content_sha256) = 32))
);

CREATE FUNCTION annotation.statements_at(p_resource bigint, p_language text, p_on date) RETURNS SETOF annotation.statement
    LANGUAGE sql STABLE
    AS $$
    SELECT s.*
      FROM annotation.statement_versions_at(p_resource, p_language, p_on) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
$$;

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

CREATE FUNCTION annotation.term_in_own_law() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

CREATE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date) RETURNS TABLE(threshold numeric, statement_id bigint, condition_id bigint)
    LANGUAGE sql STABLE
    AS $$
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
       AND NOT EXISTS (SELECT 1 FROM corpus.invalidations_at(dp.resource_id, p_language, p_on) AS i
                        WHERE i.version_id = v.id)
$$;

CREATE FUNCTION annotation.threshold_sources_at(p_condition bigint, p_language text, p_on date) RETURNS TABLE(threshold numeric, statement_id bigint, condition_id bigint, measurement_method text, averaging_period text)
    LANGUAGE sql STABLE
    AS $$
    SELECT lim.threshold, ls.id, lim.id, mm.code, ap.code
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
      LEFT JOIN vocab.concept AS mm ON mm.id = lim.measurement_method_concept_id
      LEFT JOIN vocab.concept AS ap ON ap.id = lim.averaging_period_concept_id
     WHERE c.id = p_condition
       AND lim.threshold IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
             FROM corpus.invalidations_at(dp.resource_id, p_language, p_on) AS i
            WHERE i.version_id = v.id
       )
     ORDER BY ls.id, lim.id
$$;

COMMENT ON FUNCTION annotation.threshold_sources_at(p_condition bigint, p_language text, p_on date) IS '依查詢日解析委由授權條文的所有已確認限值陳述（含檢測方法與平均時段）；供推論機依各限值之 scope 條件動態擇定適用標準。';

CREATE FUNCTION annotation.verifier_is_not_extractor() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION corpus.article_number(path text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    RETURN "substring"(path, '^art_([0-9]+(?:-[0-9]+)*)'::text);

CREATE FUNCTION corpus.path_sort_key(p text) RETURNS integer[]
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    head text := split_part(p, '/', 1);
    key integer[] := ARRAY(
        SELECT x::integer FROM regexp_split_to_table(substring(head FROM 5), '-') AS x
    );
    seg text;
BEGIN
    IF p = head THEN
        RETURN key;
    END IF;
    FOREACH seg IN ARRAY string_to_array(substring(p FROM length(head) + 2), '/') LOOP
        key := key || ARRAY[0, split_part(seg, '_', 2)::integer];
    END LOOP;
    RETURN key;
END
$$;

CREATE FUNCTION corpus.path_type(p text) RETURNS corpus.provision_type
    LANGUAGE sql IMMUTABLE
    AS $_$
    SELECT CASE
        WHEN p ~ '/sub_[0-9]+$' THEN 'subitem'
        WHEN p ~ '/item_[0-9]+$' THEN 'item'
        WHEN p ~ '/pt_[0-9]+$' THEN 'subparagraph'
        WHEN p ~ '/para_[0-9]+$' THEN 'paragraph'
        ELSE 'article'
    END::corpus.provision_type
$_$;

CREATE TABLE corpus.provision (
    id bigint NOT NULL,
    resource_id bigint NOT NULL,
    parent_id bigint,
    provision_type corpus.provision_type NOT NULL,
    path text NOT NULL,
    sort_key integer[] GENERATED ALWAYS AS (corpus.path_sort_key(path)) STORED,
    article_path text GENERATED ALWAYS AS (split_part(path, '/'::text, 1)) STORED,
    CONSTRAINT provision_only_article_is_root CHECK (((parent_id IS NULL) = (provision_type = 'article'::corpus.provision_type))),
    CONSTRAINT provision_path_shape CHECK ((path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+(/pt_[0-9]+(/item_[0-9]+(/sub_[0-9]+)?)?)?)?$'::text)),
    CONSTRAINT provision_type_matches_path CHECK ((provision_type = corpus.path_type(path)))
);

CREATE TABLE corpus.provision_version (
    id bigint NOT NULL,
    provision_id bigint NOT NULL,
    expression_id bigint NOT NULL,
    resource_id bigint NOT NULL,
    content text,
    force_period daterange,
    commencement_rule corpus.commencement_rule NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    content_sha256 bytea NOT NULL,
    language text NOT NULL,
    expression_date date NOT NULL,
    CONSTRAINT provision_version_content_check CHECK ((content <> ''::text)),
    CONSTRAINT provision_version_force_follows_rule CHECK (((force_period IS NULL) = (commencement_rule = ANY (ARRAY['by_order'::corpus.commencement_rule, 'unconfirmed'::corpus.commencement_rule])))),
    CONSTRAINT provision_version_in_force_check CHECK ((NOT isempty(force_period))),
    CONSTRAINT provision_version_sha_length CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT provision_version_start_follows_rule CHECK (((superseded_at IS NOT NULL) OR
CASE commencement_rule
    WHEN 'unchanged'::corpus.commencement_rule THEN (lower(force_period) = expression_date)
    WHEN 'promulgation'::corpus.commencement_rule THEN (lower(force_period) = (expression_date + 2))
    WHEN 'specified'::corpus.commencement_rule THEN (lower(force_period) >= expression_date)
    WHEN 'retroactive'::corpus.commencement_rule THEN (lower(force_period) < expression_date)
    ELSE (force_period IS NULL)
END)),
    CONSTRAINT provision_version_superseded_after_recorded CHECK (((superseded_at IS NULL) OR (superseded_at > recorded_at)))
);

CREATE FUNCTION corpus.article_text(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (SELECT COALESCE(v.content, (SELECT string_agg(cv.content, '
'::text ORDER BY c.sort_key) AS string_agg FROM (corpus.provision c JOIN corpus.provision_version cv ON (((cv.provision_id = c.id) AND (cv.expression_id = v.expression_id)))) WHERE ((c.resource_id = p.resource_id) AND (c.path ~~ (p.path || '/%'::text)) AND (cv.superseded_at IS NULL) AND (cv.content IS NOT NULL)))) AS "coalesce" FROM (corpus.provision_version v JOIN corpus.provision p ON ((p.id = v.provision_id))) WHERE (v.id = article_text.p_version));

CREATE FUNCTION corpus.copy_version_language() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    SELECT e.language INTO NEW.language
      FROM corpus.legal_expression AS e
     WHERE e.id = NEW.expression_id;
    IF NEW.language IS NULL THEN
        RAISE EXCEPTION 'expression % has no language', NEW.expression_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION corpus.force_within_repeal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    repeal date;
BEGIN
    IF NEW.superseded_at IS NOT NULL OR NEW.force_period IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT repealed_on INTO repeal FROM corpus.legal_resource WHERE id = NEW.resource_id;
    IF repeal IS NOT NULL AND (upper(NEW.force_period) IS NULL OR upper(NEW.force_period) > repeal) THEN
        RAISE EXCEPTION 'force period extends past repeal on %', repeal
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION corpus.invalidations_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(ruling_id bigint, ruling_no text, provision_id bigint, path text, version_id bigint, invalid_from date)
    LANGUAGE sql STABLE
    AS $$
    SELECT r.id, r.ruling_no, i.provision_id, p.path, a.version_id, i.invalid_from
      FROM corpus.ruling_invalidation AS i
      JOIN corpus.constitutional_ruling AS r ON r.id = i.ruling_id
      JOIN corpus.provision AS p ON p.id = i.provision_id
      JOIN LATERAL corpus.provisions_at(i.resource_id, p_language, p_on) AS a
           ON a.provision_id = i.provision_id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
     WHERE i.resource_id = p_resource
       AND i.invalid_from <= p_on
       AND lower(v.force_period) <= r.decided_on
     ORDER BY p.sort_key, r.decided_on, r.id
$$;

CREATE FUNCTION corpus.provision_parent_path() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
    parent_path text;
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT path INTO parent_path FROM corpus.provision WHERE id = NEW.parent_id;
    IF parent_path IS DISTINCT FROM regexp_replace(NEW.path, '/[^/]+$', '') THEN
        RAISE EXCEPTION 'parent path % does not prefix %', parent_path, NEW.path
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$_$;

CREATE FUNCTION corpus.provision_version_expression_date() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    SELECT e.version_date INTO NEW.expression_date
      FROM corpus.legal_expression AS e
     WHERE e.id = NEW.expression_id;
    RETURN NEW;
END
$$;

CREATE FUNCTION corpus.provision_version_hash() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(coalesce(NEW.content, ''), 'UTF8'));
    RETURN NEW;
END
$$;

CREATE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date) RETURNS TABLE(provision_id bigint, path text, provision_type corpus.provision_type, sort_key integer[], version_id bigint, status corpus.provision_status)
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

CREATE FUNCTION corpus.repeal_caps_force() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.repealed_on IS NOT NULL AND EXISTS (
        SELECT 1 FROM corpus.provision_version AS v
         WHERE v.resource_id = NEW.id
           AND v.superseded_at IS NULL
           AND v.force_period IS NOT NULL
           AND (upper(v.force_period) IS NULL OR upper(v.force_period) > NEW.repealed_on)
    ) THEN
        RAISE EXCEPTION 'repeal on % leaves a current version in force', NEW.repealed_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION corpus.resource_iri(kind corpus.norm_kind, local_id text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    RETURN ((('https://envcite/tw/'::text || CASE WHEN (kind = ANY (ARRAY['constitution'::corpus.norm_kind, 'statute'::corpus.norm_kind])) THEN 'law'::text ELSE 'order'::text END) || '/'::text) || local_id);

CREATE FUNCTION corpus.ruling_invalidation_dated() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.invalid_from < (SELECT decided_on FROM corpus.constitutional_ruling WHERE id = NEW.ruling_id) THEN
        RAISE EXCEPTION 'invalid_from % precedes the ruling date', NEW.invalid_from;
    END IF;
    RETURN NEW;
END $$;

CREATE TABLE corpus.legal_expression (
    id bigint NOT NULL,
    resource_id bigint NOT NULL,
    language text NOT NULL,
    version_date date NOT NULL,
    title text,
    text_period daterange NOT NULL,
    text_status corpus.text_status NOT NULL,
    text_authority corpus.text_authority NOT NULL,
    text_until date GENERATED ALWAYS AS (upper(text_period)) STORED,
    CONSTRAINT legal_expression_check CHECK (((NOT isempty(text_period)) AND (lower(text_period) = version_date))),
    CONSTRAINT legal_expression_language_check CHECK ((language ~ '^[a-z]{3}$'::text)),
    CONSTRAINT legal_expression_title_check CHECK ((title <> ''::text)),
    CONSTRAINT legal_expression_title_known CHECK (((title IS NOT NULL) OR (text_status = 'unavailable'::corpus.text_status)))
);

CREATE TABLE corpus.legal_resource (
    id bigint NOT NULL,
    id_scheme corpus.id_scheme NOT NULL,
    local_id text NOT NULL,
    norm_kind corpus.norm_kind NOT NULL,
    document_type text NOT NULL,
    repealed_on date,
    jurisdiction corpus.jurisdiction NOT NULL,
    CONSTRAINT legal_resource_document_type_check CHECK ((document_type <> ''::text)),
    CONSTRAINT legal_resource_pcode_shape CHECK (((id_scheme <> 'moj_pcode'::corpus.id_scheme) OR (local_id ~ '^[A-Z][0-9]{7}$'::text))),
    CONSTRAINT legal_resource_scheme_matches_jurisdiction CHECK (
CASE id_scheme
    WHEN 'moj_pcode'::corpus.id_scheme THEN (jurisdiction = 'TW-CENTRAL'::corpus.jurisdiction)
    WHEN 'local_glrs'::corpus.id_scheme THEN (jurisdiction <> 'TW-CENTRAL'::corpus.jurisdiction)
    ELSE false
END)
);

COMMENT ON COLUMN corpus.legal_resource.jurisdiction IS '管轄；與識別碼方案配對（legal_resource_scheme_matches_jurisdiction）。沒有預設值。';

CREATE FUNCTION corpus.version_iri(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (SELECT ((((((corpus.resource_iri(r.norm_kind, r.local_id) || '/'::text) || e.version_date) || '/'::text) || e.language) || '/'::text) || p.path) FROM (((corpus.provision_version v JOIN corpus.provision p ON ((p.id = v.provision_id))) JOIN corpus.legal_expression e ON ((e.id = v.expression_id))) JOIN corpus.legal_resource r ON ((r.id = v.resource_id))) WHERE (v.id = version_iri.p_version));

CREATE FUNCTION guidance.document_hash() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(NEW.subject || E'\n' || NEW.body, 'UTF8'));
    RETURN NEW;
END
$$;

CREATE FUNCTION guidance.for_statements(p_statements bigint[], p_decided date) RETURNS TABLE(document_id bigint, statement_id bigint)
    LANGUAGE sql STABLE
    AS $$
    SELECT d.id, s.id
      FROM annotation.statement AS s
      JOIN guidance.interpretation AS i ON i.provision_id = s.provision_id
                                       AND i.content_sha256 = s.content_sha256
      JOIN guidance.document AS d ON d.id = i.document_id
     WHERE s.id = ANY(p_statements)
       AND d.issued_on <= p_decided
       AND NOT EXISTS (SELECT 1 FROM guidance.cessation AS c
                        WHERE c.document_id = d.id AND c.ceased_on <= p_decided)
     ORDER BY d.issued_on, d.id, s.id
$$;

CREATE FUNCTION guidance.loaded_by_people_or_rules() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (SELECT method FROM annotation.derivation WHERE id = NEW.derivation_id) = 'llm' THEN
        RAISE EXCEPTION 'administrative rules are loaded by people or rules, not extracted by a model'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION guidance.name_key(p_name text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    RETURN regexp_replace(NORMALIZE(p_name, NFKC), '\s+'::text, ''::text, 'g'::text);

CREATE FUNCTION guidance.settle_crawl(p_seen text[], p_on date, p_source text) RETURNS TABLE(complete boolean, dropped integer, ceased integer, reappeared bigint[])
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_active integer;
    v_missing integer;
    v_dropped text[];
BEGIN
    SELECT count(*), count(*) FILTER (WHERE NOT i.key = ANY(p_seen))
      INTO v_active, v_missing
      FROM source.item AS i WHERE i.feed = 'moenv_guidance' AND i.dropped_on IS NULL;
    IF v_missing > 0.05 * v_active THEN
        RETURN QUERY SELECT false, 0, 0, '{}'::bigint[];
        RETURN;
    END IF;
    UPDATE source.item SET missed_crawls = 0
     WHERE feed = 'moenv_guidance' AND dropped_on IS NULL AND key = ANY(p_seen);
    WITH missed AS (
        UPDATE source.item
           SET missed_crawls = missed_crawls + 1,
               dropped_on = CASE WHEN missed_crawls + 1 = 2 THEN p_on END
         WHERE feed = 'moenv_guidance' AND dropped_on IS NULL AND NOT key = ANY(p_seen)
        RETURNING key, dropped_on
    )
    SELECT coalesce(array_agg(key) FILTER (WHERE dropped_on IS NOT NULL), '{}') INTO v_dropped
      FROM missed;
    RETURN QUERY
    WITH ceased AS (
        INSERT INTO guidance.cessation
            (document_id, issued_on, ceased_on, cause, dropped_feed, dropped_key)
        SELECT d.id, d.issued_on, p_on, 'taken_down', 'moenv_guidance', d.document_no
          FROM guidance.document AS d
         WHERE d.document_no = ANY(v_dropped) AND starts_with(d.source_url, p_source)
        ON CONFLICT (document_id) DO NOTHING
        RETURNING 1
    )
    SELECT true, cardinality(v_dropped), (SELECT count(*)::integer FROM ceased),
           (SELECT coalesce(array_agg(i.id ORDER BY i.id), '{}') FROM source.item AS i
             WHERE i.feed = 'moenv_guidance' AND i.dropped_on IS NOT NULL AND i.key = ANY(p_seen));
END
$$;

CREATE FUNCTION judgment.decision_hash() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(NEW.title || E'\n' || NEW.body, 'UTF8'));
    RETURN NEW;
END
$$;

CREATE FUNCTION judgment.for_statements(p_statements bigint[], p_decided date) RETURNS TABLE(decision_id bigint, statement_id bigint)
    LANGUAGE sql STABLE
    AS $$
    SELECT d.id, s.id
      FROM annotation.statement AS s
      JOIN judgment.anchor AS a ON a.provision_id = s.provision_id
                               AND a.content_sha256 = s.content_sha256
      JOIN judgment.decision AS d ON d.id = a.decision_id
     WHERE s.id = ANY(p_statements)
       AND d.decided_on <= p_decided
     ORDER BY d.decided_on, d.id, s.id
$$;

CREATE FUNCTION judgment.loaded_by_people_or_rules() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (SELECT method FROM annotation.derivation WHERE id = NEW.derivation_id) = 'llm' THEN
        RAISE EXCEPTION 'judgments are loaded by people or rules, not extracted by a model'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION retrieval.guidance_articles(p_phrases text[], p_as_of date, p_language text, p_limit integer, p_max_df double precision) RETURNS TABLE(version_id bigint, resource_id bigint, path text, documents integer)
    LANGUAGE sql STABLE
    AS $$
    WITH eligible AS (
        SELECT d.id, d.subject, d.body
          FROM guidance.document AS d
         WHERE d.issued_on <= p_as_of
           AND NOT EXISTS (SELECT 1 FROM guidance.cessation AS c
                            WHERE c.document_id = d.id AND c.ceased_on <= p_as_of)
           AND EXISTS (SELECT 1 FROM guidance.interpretation AS i WHERE i.document_id = d.id)
    ),
    matching AS (
        SELECT e.id
          FROM eligible AS e
         WHERE cardinality(p_phrases) > 0
           AND NOT EXISTS (SELECT 1 FROM unnest(p_phrases) AS k
                            WHERE position(k IN e.subject) = 0 AND position(k IN e.body) = 0)
    ),
    docs AS (
        SELECT m.id
          FROM matching AS m
         WHERE p_max_df <= 0
            OR (SELECT count(*) FROM matching)::double precision
               <= p_max_df * (SELECT count(*) FROM eligible)
    ),
    anchored AS (
        SELECT p.resource_id, p.article_path, count(DISTINCT docs.id)::integer AS documents
          FROM docs
          JOIN guidance.interpretation AS i ON i.document_id = docs.id
          JOIN corpus.provision_version AS v ON v.provision_id = i.provision_id
                                            AND v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
    )
    SELECT av.id, a.resource_id, a.article_path, a.documents
      FROM anchored AS a
      JOIN corpus.provision AS ap ON ap.resource_id = a.resource_id AND ap.path = a.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY a.documents DESC, av.id
     LIMIT p_limit
$$;

COMMENT ON FUNCTION retrieval.guidance_articles(p_phrases text[], p_as_of date, p_language text, p_limit integer, p_max_df double precision) IS '詞組 → 函釋（主旨或內文含每個詞組、在該日有效）所解釋的條文，被越多件函釋帶出的在前。命中的函釋占可用函釋的比例超過 p_max_df 時不回任何條（0 為不限制）。函釋只用來找條文，不是依據。';

CREATE FUNCTION retrieval.law_scope(p_laws text[], p_language text, p_on date) RETURNS bigint[]
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE scope(id) AS (
        SELECT r.id
          FROM corpus.legal_resource AS r
         WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(p_laws)
        UNION
        SELECT d.command_resource_id
          FROM scope AS s
         CROSS JOIN LATERAL annotation.delegated_by(s.id, p_language, p_on) AS d
    )
    SELECT array_agg(id ORDER BY id) FROM scope
$$;

COMMENT ON FUNCTION retrieval.law_scope(p_laws text[], p_language text, p_on date) IS '法規代碼 → 檢索範圍（法規內部編號）：這些法規與依其授權（遞迴）訂定、在該日有效的命令。';

CREATE FUNCTION retrieval.prune() RETURNS bigint
    LANGUAGE sql
    AS $$
    WITH gone AS (
        DELETE FROM retrieval.passage AS i
         WHERE NOT EXISTS (SELECT 1 FROM corpus.provision_version AS v
                            WHERE v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL)
        RETURNING 1
    )
    SELECT count(*) FROM gone
$$;

CREATE FUNCTION retrieval.prune_terms(p_terms text, p_tokenizer text, p_max_df double precision) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    WITH total AS (
        SELECT passages FROM retrieval.term_stat_total WHERE tokenizer = p_tokenizer
    ),
    w AS (
        SELECT t.word, t.ord, coalesce(s.doc_freq, 0) AS df
          FROM unnest(string_to_array(btrim(p_terms), ' ')) WITH ORDINALITY AS t(word, ord)
          LEFT JOIN retrieval.term_stat AS s
                 ON s.tokenizer = p_tokenizer AND s.term = lower(t.word)
         WHERE btrim(t.word) <> ''
    ),
    common AS (
        SELECT w.word, w.ord FROM w CROSS JOIN total WHERE w.df <= p_max_df * total.passages
    ),
    rarest AS (
        SELECT w.word, w.ord FROM w ORDER BY w.df, w.ord LIMIT 1
    )
    SELECT CASE
        WHEN p_max_df IS NULL OR p_max_df <= 0 OR NOT EXISTS (SELECT 1 FROM total) THEN p_terms
        ELSE coalesce(
            (SELECT string_agg(k.word, ' ' ORDER BY k.ord)
               FROM (SELECT * FROM common
                     UNION ALL
                     SELECT * FROM rarest WHERE NOT EXISTS (SELECT 1 FROM common)) AS k),
            p_terms)
    END
$$;

CREATE FUNCTION retrieval.refresh_term_stat(p_tokenizer text) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM retrieval.term_stat;
    DELETE FROM retrieval.term_stat_total;
    INSERT INTO retrieval.term_stat (tokenizer, term, doc_freq)
        SELECT p_tokenizer, s.word, s.ndoc
          FROM ts_stat(format('SELECT tsv FROM retrieval.passage WHERE tokenizer = %L', p_tokenizer)) AS s;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO retrieval.term_stat_total (tokenizer, passages)
        SELECT p_tokenizer, count(*) FROM retrieval.passage WHERE tokenizer = p_tokenizer;
    RETURN n;
END
$$;

CREATE FUNCTION retrieval.sanctions_for(p_resource bigint, p_article text, p_language text, p_on date) RETURNS TABLE(resource_id bigint, article_path text)
    LANGUAGE sql STABLE
    AS $$
    WITH RECURSIVE target(resource_id, article_path) AS (
        SELECT p_resource, p_article
        UNION
        SELECT d.law_resource_id, lp.article_path
          FROM target AS t
         CROSS JOIN LATERAL annotation.delegations_at(t.resource_id, p_language, p_on) AS d
          JOIN corpus.provision AS lp ON lp.id = d.law_provision_id
    )
    SELECT DISTINCT pp.resource_id, pp.article_path
      FROM target AS t
      JOIN corpus.provision AS cp ON cp.resource_id = t.resource_id AND cp.article_path = t.article_path
      JOIN annotation.current_violation AS vi ON vi.cited_provision_id = cp.id
      JOIN corpus.provision AS pp ON pp.id = vi.provision_id
      JOIN corpus.provision_version AS v
        ON v.provision_id = vi.provision_id AND v.content_sha256 = vi.content_sha256
       AND v.superseded_at IS NULL AND v.force_period @> p_on AND v.language = p_language
     WHERE NOT (pp.resource_id = t.resource_id AND pp.article_path = t.article_path)
$$;

CREATE FUNCTION retrieval.search_expanded(p_terms text, p_tokenizer text, p_embedding public.vector, p_model text, p_as_of date, p_language text, p_limit integer, p_pool integer, p_resources bigint[]) RETURNS TABLE(version_id bigint, resource_id bigint, path text, lexical_score real, semantic_similarity real, rank integer)
    LANGUAGE sql STABLE
    AS $$
    WITH hit AS MATERIALIZED (
        SELECT h.*, row_number() OVER (ORDER BY h.rank_score DESC, h.version_id) AS r
          FROM retrieval.search_hybrid(p_terms, p_tokenizer, p_embedding, p_model, p_as_of,
                                       p_language, p_limit, p_pool, p_resources) AS h
    ),
    candidate AS (
        SELECT h.resource_id, h.path, h.lexical_score, h.semantic_similarity, h.r, 0 AS k
          FROM hit AS h
        UNION ALL
        SELECT s.resource_id, s.article_path, h.lexical_score, h.semantic_similarity, h.r, 1
          FROM hit AS h
         CROSS JOIN LATERAL retrieval.sanctions_for(h.resource_id, h.path, p_language, p_as_of) AS s
    ),
    first AS (
        SELECT DISTINCT ON (c.resource_id, c.path) c.*
          FROM candidate AS c
         ORDER BY c.resource_id, c.path, c.r, c.k
    )
    SELECT av.id, f.resource_id, f.path, f.lexical_score, f.semantic_similarity,
           (row_number() OVER (ORDER BY f.r, f.k, f.resource_id, f.path))::integer
      FROM first AS f
      JOIN corpus.provision AS ap ON ap.resource_id = f.resource_id AND ap.path = f.path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY f.r, f.k, f.resource_id, f.path
     LIMIT p_limit
$$;

CREATE FUNCTION retrieval.search_hybrid(p_terms text, p_tokenizer text, p_embedding public.vector, p_model text, p_as_of date, p_language text, p_limit integer, p_pool integer, p_resources bigint[]) RETURNS TABLE(version_id bigint, resource_id bigint, path text, lexical_score real, semantic_similarity real, rank_score double precision)
    LANGUAGE sql STABLE
    AS $$
    WITH scope AS MATERIALIZED (SELECT p_resources AS ids),
    q AS MATERIALIZED (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    lex AS (
        SELECT p.resource_id, p.article_path, max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.passage AS i
         CROSS JOIN q
          JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE i.tokenizer = p_tokenizer
           AND i.tsv @@ q.query
           AND ((SELECT ids FROM scope) IS NULL OR p.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
           AND v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
         ORDER BY score DESC, p.resource_id, p.article_path
         LIMIT p_pool
    ),
    in_scope AS MATERIALIZED (
        SELECT i.content_sha256, i.embedding
          FROM retrieval.passage AS i
         WHERE (SELECT ids FROM scope) IS NOT NULL AND p_embedding IS NOT NULL AND i.model = p_model
           AND i.content_sha256 IN (SELECT v.content_sha256
                                      FROM corpus.provision_version AS v
                                     WHERE v.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
    ),
    near AS (
        (SELECT i.content_sha256, 1 - (i.embedding <=> p_embedding) AS similarity
           FROM retrieval.passage AS i
          WHERE (SELECT ids FROM scope) IS NULL AND p_embedding IS NOT NULL AND i.model = p_model
          ORDER BY i.embedding <=> p_embedding
          LIMIT p_pool * 4)
        UNION ALL
        (SELECT s.content_sha256, 1 - (s.embedding <=> p_embedding) AS similarity
           FROM in_scope AS s
          ORDER BY s.embedding <=> p_embedding
          LIMIT p_pool * 4)
    ),
    sem AS (
        SELECT p.resource_id, p.article_path, max(n.similarity) AS score
          FROM near AS n
          JOIN corpus.provision_version AS v ON v.content_sha256 = n.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE ((SELECT ids FROM scope) IS NULL OR p.resource_id = ANY((SELECT ids FROM scope)::bigint[]))
           AND v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, p.article_path
         ORDER BY score DESC, p.resource_id, p.article_path
         LIMIT p_pool
    ),
    fused AS (
        SELECT coalesce(l.resource_id, s.resource_id) AS resource_id,
               coalesce(l.article_path, s.article_path) AS article_path,
               l.score AS lexical, s.score AS semantic,
               coalesce(1.0 / (60 + l.r), 0) + coalesce(1.0 / (60 + s.r), 0) AS rank_score
          FROM (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r FROM lex) AS l
          FULL JOIN (SELECT *, row_number() OVER (ORDER BY score DESC, resource_id, article_path) AS r FROM sem) AS s
            ON s.resource_id = l.resource_id AND s.article_path = l.article_path
    )
    SELECT av.id, f.resource_id, f.article_path,
           coalesce(f.lexical, 0)::real, f.semantic::real, f.rank_score::double precision
      FROM fused AS f
      JOIN corpus.provision AS ap ON ap.resource_id = f.resource_id AND ap.path = f.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY f.rank_score DESC, av.id
     LIMIT p_limit
$$;

CREATE FUNCTION retrieval.terms_to_tsquery(terms text) RETURNS tsquery
    LANGUAGE sql IMMUTABLE STRICT
    RETURN COALESCE(((SELECT string_agg((plainto_tsquery('simple'::regconfig, w.w))::text, ' | '::text) AS string_agg FROM unnest(string_to_array(btrim(terms_to_tsquery.terms), ' '::text)) w(w) WHERE ((btrim(w.w) <> ''::text) AND ((plainto_tsquery('simple'::regconfig, w.w))::text <> ''::text))))::tsquery, ''::tsquery);

CREATE TABLE annotation.assertion (
    id bigint NOT NULL,
    assertion_kind annotation.assertion_kind NOT NULL,
    derivation_id bigint NOT NULL,
    confirm_state annotation.confirm_state DEFAULT 'extracted'::annotation.confirm_state NOT NULL,
    fingerprint bytea,
    CONSTRAINT assertion_fingerprint_check CHECK ((octet_length(fingerprint) = 32))
);

COMMENT ON COLUMN annotation.assertion.fingerprint IS '內容指紋（annotation.claim_fingerprint）；由 annotation.seal 寫入，寫入後內容不可再補。';

ALTER TABLE annotation.assertion ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.assertion_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE annotation.violation (
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    cited_path text NOT NULL,
    cited_provision_id bigint,
    violation_kind annotation.violation_kind NOT NULL,
    rule_version integer NOT NULL,
    CONSTRAINT violation_cited_path_check CHECK ((cited_path <> ''::text)),
    CONSTRAINT violation_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT violation_rule_version_check CHECK ((rule_version > 0))
);

CREATE VIEW annotation.current_violation AS
 SELECT provision_id,
    content_sha256,
    cited_path,
    cited_provision_id,
    violation_kind,
    rule_version
   FROM annotation.violation vi
  WHERE (rule_version = ( SELECT max(x.rule_version) AS max
           FROM annotation.violation x
          WHERE ((x.provision_id = vi.provision_id) AND (x.content_sha256 = vi.content_sha256))));

CREATE TABLE annotation.delegation (
    id bigint NOT NULL,
    command_resource_id bigint NOT NULL,
    command_provision_id bigint,
    command_content_sha256 bytea,
    cited_law_name text NOT NULL,
    cited_path text NOT NULL,
    law_resource_id bigint,
    law_provision_id bigint,
    assertion_kind annotation.assertion_kind GENERATED ALWAYS AS ('delegation'::annotation.assertion_kind) STORED,
    CONSTRAINT delegation_anchor_whole CHECK (((command_provision_id IS NULL) = (command_content_sha256 IS NULL))),
    CONSTRAINT delegation_cited_law_name_check CHECK (((cited_law_name = btrim(cited_law_name)) AND (cited_law_name <> ''::text))),
    CONSTRAINT delegation_cited_path_check CHECK ((cited_path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+)?$'::text)),
    CONSTRAINT delegation_command_content_sha256_check CHECK ((octet_length(command_content_sha256) = 32)),
    CONSTRAINT delegation_law_before_provision CHECK (((law_provision_id IS NULL) OR (law_resource_id IS NOT NULL))),
    CONSTRAINT delegation_not_self CHECK ((law_resource_id IS DISTINCT FROM command_resource_id))
);

COMMENT ON TABLE annotation.delegation IS '授權：命令（命令端）依法律（法律端）某條文訂定。不固定目標版本，依查詢日解析。';

COMMENT ON COLUMN annotation.delegation.law_resource_id IS '法名對到的法規；對不到（無此名稱、對到數部）為空，不猜。';

COMMENT ON COLUMN annotation.delegation.law_provision_id IS '授權條文單元；法規對到了但沒有該條文單元時為空。';

CREATE TABLE annotation.derivation (
    id bigint NOT NULL,
    method annotation.derivation_method NOT NULL,
    producer text NOT NULL,
    producer_version text NOT NULL,
    run_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT derivation_check CHECK (((producer <> ''::text) AND (producer_version <> ''::text)))
);

CREATE VIEW annotation.delegation_usable AS
 SELECT g.id,
    g.command_resource_id,
    g.command_provision_id,
    g.command_content_sha256,
    g.cited_law_name,
    g.cited_path,
    g.law_resource_id,
    g.law_provision_id,
    g.assertion_kind
   FROM ((annotation.delegation g
     JOIN annotation.assertion c ON ((c.id = g.id)))
     JOIN annotation.derivation d ON ((d.id = c.derivation_id)))
  WHERE ((c.confirm_state <> 'rejected'::annotation.confirm_state) AND ((c.confirm_state = 'confirmed'::annotation.confirm_state) OR (d.method = 'rule'::annotation.derivation_method)) AND (NOT ((g.law_resource_id IS NULL) AND (EXISTS ( SELECT 1
           FROM ((annotation.delegation h
             JOIN annotation.assertion hc ON ((hc.id = h.id)))
             JOIN annotation.derivation hd ON ((hd.id = hc.derivation_id)))
          WHERE ((h.command_resource_id = g.command_resource_id) AND (NOT (h.command_provision_id IS DISTINCT FROM g.command_provision_id)) AND (NOT (h.command_content_sha256 IS DISTINCT FROM g.command_content_sha256)) AND (h.cited_law_name = g.cited_law_name) AND (h.cited_path = g.cited_path) AND (h.law_resource_id IS NOT NULL) AND (hc.confirm_state <> 'rejected'::annotation.confirm_state) AND ((hc.confirm_state = 'confirmed'::annotation.confirm_state) OR (hd.method = 'rule'::annotation.derivation_method))))))));

ALTER TABLE annotation.derivation ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.derivation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE VIEW annotation.duty_gap AS
 SELECT DISTINCT c.id AS provision_id,
    c.path,
    vi.provision_id AS penalty_provision_id
   FROM ((annotation.current_violation vi
     JOIN corpus.provision_version pv ON (((pv.provision_id = vi.provision_id) AND (pv.content_sha256 = vi.content_sha256) AND (pv.superseded_at IS NULL))))
     JOIN corpus.provision c ON ((c.id = vi.cited_provision_id)))
  WHERE ((vi.violation_kind = 'duty'::annotation.violation_kind) AND (NOT (EXISTS ( SELECT 1
           FROM (((corpus.provision p
             JOIN annotation.statement s ON ((s.provision_id = p.id)))
             JOIN corpus.provision_version v ON (((v.provision_id = s.provision_id) AND (v.content_sha256 = s.content_sha256) AND (v.superseded_at IS NULL))))
             JOIN annotation.assertion a ON ((a.id = s.id)))
          WHERE ((p.resource_id = c.resource_id) AND ((p.path = c.path) OR (p.path ~~ (c.path || '/%'::text)) OR (c.path ~~ (p.path || '/%'::text))) AND (s.statement_kind = 'prescriptive'::annotation.statement_kind) AND (s.norm_modality = ANY (ARRAY['obligation'::annotation.norm_modality, 'prohibition'::annotation.norm_modality])) AND (a.confirm_state <> 'rejected'::annotation.confirm_state))))));

CREATE TABLE annotation.extraction_attempt (
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    producer text NOT NULL,
    producer_version text NOT NULL,
    statement_count integer NOT NULL,
    dropped_count integer NOT NULL,
    attempted_at timestamp with time zone DEFAULT now() NOT NULL,
    round smallint DEFAULT 0 NOT NULL,
    CONSTRAINT extraction_attempt_check CHECK (((producer <> ''::text) AND (producer_version <> ''::text))),
    CONSTRAINT extraction_attempt_check1 CHECK (((statement_count >= 0) AND (dropped_count >= 0))),
    CONSTRAINT extraction_attempt_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT extraction_attempt_round_range CHECK (((round >= 0) AND (round <= 3)))
);

COMMENT ON TABLE annotation.extraction_attempt IS '模型對某單元某文字答過一次（不論產出幾個陳述）；管線據此不重複抽取。';

COMMENT ON COLUMN annotation.extraction_attempt.statement_count IS '本次寫入的陳述數，可為 0。';

COMMENT ON COLUMN annotation.extraction_attempt.dropped_count IS '被丟掉的陳述數（引文不在原文、違反約束等）。';

COMMENT ON COLUMN annotation.extraction_attempt.round IS '第幾輪抽取：0 是首次，1 以上是帶審查意見的重抽（上限 3）。';

CREATE TABLE annotation.verification (
    id bigint NOT NULL,
    assertion_id bigint NOT NULL,
    method text NOT NULL,
    verifier text NOT NULL,
    result annotation.gate_result NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    verified_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    recorded_by text DEFAULT SESSION_USER NOT NULL,
    recorded_in xid8 DEFAULT pg_current_xact_id() NOT NULL,
    CONSTRAINT verification_method_check CHECK ((method <> ''::text)),
    CONSTRAINT verification_verifier_check CHECK ((verifier <> ''::text))
);

COMMENT ON COLUMN annotation.verification.recorded_by IS '寫入這列的登入身分（session_user）；verifier 是自報的方法或閘門名稱，這欄不是。';

COMMENT ON COLUMN annotation.verification.recorded_in IS '寫入這列的交易；觸發器據此判斷狀態變更是否已由 settle 或 set_confirm_state 記過。';

CREATE VIEW annotation.gate_health AS
 SELECT method,
    (verdict)::text AS verdict,
    count(*) AS statements
   FROM ( SELECT DISTINCT ON (v.assertion_id, v.method) v.assertion_id AS claim_id,
            v.method,
            v.result AS verdict
           FROM (annotation.verification v
             JOIN annotation.assertion c ON ((c.id = v.assertion_id)))
          WHERE ((v.method <> 'policy'::text) AND (c.assertion_kind = 'statement'::annotation.assertion_kind))
          ORDER BY v.assertion_id, v.method, v.id DESC) latest
  GROUP BY method, verdict;

COMMENT ON VIEW annotation.gate_health IS '各驗證方法對規範陳述最近一次的結果分布：pass、fail、inconclusive、not_applicable。';

CREATE TABLE annotation.held_penalty (
    id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    producer text NOT NULL,
    producer_version text NOT NULL,
    punished_statement_id bigint,
    document jsonb NOT NULL,
    fingerprint bytea NOT NULL,
    held_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT held_penalty_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT held_penalty_document_check CHECK ((jsonb_typeof((document -> 'statements'::text)) = 'array'::text)),
    CONSTRAINT held_penalty_fingerprint_check CHECK ((octet_length(fingerprint) = 32)),
    CONSTRAINT held_penalty_producer_check CHECK ((producer <> ''::text)),
    CONSTRAINT held_penalty_producer_version_check CHECK ((producer_version <> ''::text))
);

ALTER TABLE annotation.held_penalty ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.held_penalty_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE annotation.held_penalty_link (
    held_penalty_id bigint NOT NULL,
    statement_id bigint NOT NULL
);

CREATE VIEW annotation.held_penalty_state AS
 SELECT id,
    provision_id,
    content_sha256,
    producer,
    producer_version,
        CASE
            WHEN (EXISTS ( SELECT 1
               FROM annotation.held_penalty_link l
              WHERE (l.held_penalty_id = h.id))) THEN 'linked'::text
            WHEN (EXISTS ( SELECT 1
               FROM annotation.penalty_candidates(h.id) c(statement_id, path, confirmed)
              WHERE c.confirmed)) THEN 'linkable'::text
            WHEN (EXISTS ( SELECT 1
               FROM annotation.penalty_candidates(h.id) penalty_candidates(statement_id, path, confirmed))) THEN 'awaiting_duty'::text
            WHEN ((punished_statement_id IS NULL) AND (NOT (EXISTS ( SELECT 1
               FROM annotation.current_violation vi
              WHERE ((vi.provision_id = h.provision_id) AND (vi.content_sha256 = h.content_sha256) AND (vi.violation_kind = 'duty'::annotation.violation_kind) AND (vi.cited_provision_id IS NOT NULL)))))) THEN 'no_citation'::text
            ELSE 'no_duty'::text
        END AS state
   FROM annotation.held_penalty h;

CREATE TABLE annotation.mutatis_mutandis (
    id bigint NOT NULL,
    source_statement_id bigint NOT NULL,
    target_statement_id bigint NOT NULL,
    CONSTRAINT mutatis_mutandis_check CHECK ((source_statement_id <> target_statement_id))
);

COMMENT ON TABLE annotation.mutatis_mutandis IS '準用關係（中央法規標準法第 17 條）：source_statement 準用 target_statement。';

ALTER TABLE annotation.mutatis_mutandis ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.mutatis_mutandis_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE VIEW annotation.unresolved_duty AS
 SELECT s.id AS statement_id,
    s.provision_id,
    s.content_sha256
   FROM ((annotation.statement s
     JOIN annotation.assertion a ON ((a.id = s.id)))
     JOIN corpus.provision_version v ON (((v.provision_id = s.provision_id) AND (v.content_sha256 = s.content_sha256) AND (v.superseded_at IS NULL))))
  WHERE ((s.statement_kind = 'prescriptive'::annotation.statement_kind) AND (s.norm_modality = ANY (ARRAY['obligation'::annotation.norm_modality, 'prohibition'::annotation.norm_modality])) AND (a.confirm_state <> 'confirmed'::annotation.confirm_state) AND (NOT (EXISTS ( SELECT 1
           FROM (annotation.statement c
             JOIN annotation.assertion ca ON ((ca.id = c.id)))
          WHERE ((c.provision_id = s.provision_id) AND (c.content_sha256 = s.content_sha256) AND (c.statement_kind = 'prescriptive'::annotation.statement_kind) AND (c.norm_modality = ANY (ARRAY['obligation'::annotation.norm_modality, 'prohibition'::annotation.norm_modality])) AND (ca.confirm_state = 'confirmed'::annotation.confirm_state) AND ((c.span IS NULL) OR (s.span IS NULL) OR (c.span && s.span)))))));

COMMENT ON VIEW annotation.unresolved_duty IS '單元現行文字上沒有定論的義務或禁止：未確認，且沒有已確認的義務或禁止涵蓋同一段引文。';

CREATE VIEW annotation.reextraction_due AS
 SELECT a.provision_id,
    a.content_sha256,
    a.producer,
    a.producer_version,
    ((max(a.round) + 1))::smallint AS next_round
   FROM (annotation.extraction_attempt a
     JOIN corpus.provision up ON ((up.id = a.provision_id)))
  WHERE ((EXISTS ( SELECT 1
           FROM annotation.unresolved_duty u
          WHERE ((u.provision_id = a.provision_id) AND (u.content_sha256 = a.content_sha256)))) OR (EXISTS ( SELECT 1
           FROM (annotation.duty_gap g
             JOIN corpus.provision gp ON ((gp.id = g.provision_id)))
          WHERE ((gp.resource_id = up.resource_id) AND ((up.path = gp.path) OR (up.path ~~ (gp.path || '/%'::text)))))))
  GROUP BY a.provision_id, a.content_sha256, a.producer, a.producer_version
 HAVING (max(a.round) < 3);

COMMENT ON VIEW annotation.reextraction_due IS '義務沒有定論，或被處罰條文引用卻沒有義務，且還有輪數可重抽的（單元，抽取者，版本）；next_round 是下一輪的編號。';

CREATE TABLE annotation.sanction (
    statement_id bigint NOT NULL,
    punished_statement_id bigint NOT NULL,
    legal_effect annotation.legal_effect NOT NULL,
    amount_text text,
    min_amount numeric(18,0),
    max_amount numeric(18,0),
    per_occasion boolean DEFAULT false NOT NULL,
    discretionary boolean DEFAULT false NOT NULL,
    statement_kind annotation.statement_kind GENERATED ALWAYS AS ('sanction'::annotation.statement_kind) STORED,
    punished_statement_kind annotation.statement_kind GENERATED ALWAYS AS ('prescriptive'::annotation.statement_kind) STORED,
    CONSTRAINT sanction_check CHECK (((min_amount IS NULL) OR (max_amount IS NULL) OR (min_amount <= max_amount))),
    CONSTRAINT sanction_check1 CHECK ((statement_id <> punished_statement_id))
);

COMMENT ON COLUMN annotation.sanction.legal_effect IS 'fine、confiscation 為行政罰法第 1 條；restriction、deprivation、reputation、warning 為同法第 2 條四類其他種類行政罰；order（改善命令等）是預防性（管制性）不利處分，不是行政罰，行政罰法的總則不適用。';

COMMENT ON COLUMN annotation.sanction.discretionary IS '效果裁量：條文寫「得」處罰或命令者為真，寫「應」者為假。與要件的 needs_assessment 是兩個層次。';

CREATE TABLE annotation.sanction_trigger (
    statement_id bigint NOT NULL,
    trigger_statement_id bigint NOT NULL,
    trigger_kind annotation.trigger_kind NOT NULL,
    trigger_effect annotation.legal_effect GENERATED ALWAYS AS (
CASE
    WHEN (trigger_kind = 'order_lapsed'::annotation.trigger_kind) THEN 'order'::annotation.legal_effect
    ELSE NULL::annotation.legal_effect
END) STORED,
    CONSTRAINT sanction_trigger_check CHECK ((statement_id <> trigger_statement_id))
);

COMMENT ON TABLE annotation.sanction_trigger IS '罰則 statement_id 以罰則 trigger_statement_id 的作成（或其命令屆期未履行）為前提。';

CREATE TABLE annotation.statement_condition (
    id bigint NOT NULL,
    statement_id bigint NOT NULL,
    condition_role annotation.condition_role NOT NULL,
    disjunct_no smallint NOT NULL,
    concept_id bigint NOT NULL,
    comparator annotation.comparator NOT NULL,
    threshold numeric(18,3),
    measure_unit text,
    value_concept_id bigint,
    negated boolean DEFAULT false NOT NULL,
    delegate_provision_id bigint,
    needs_assessment boolean DEFAULT false NOT NULL,
    delegate_permit_concept_id bigint,
    measurement_method_concept_id bigint,
    averaging_period_concept_id bigint,
    carried boolean DEFAULT false NOT NULL,
    CONSTRAINT statement_condition_carried_is_plain_scope CHECK (((NOT carried) OR ((condition_role = 'scope'::annotation.condition_role) AND (comparator = 'eq'::annotation.comparator) AND (threshold = (1)::numeric) AND (NOT negated) AND (NOT needs_assessment)))),
    CONSTRAINT statement_condition_check1 CHECK (((condition_role <> 'limit'::annotation.condition_role) OR (threshold IS NOT NULL))),
    CONSTRAINT statement_condition_limit_is_not_assessment CHECK (((condition_role <> 'limit'::annotation.condition_role) OR (NOT needs_assessment))),
    CONSTRAINT statement_condition_measurement_is_limit CHECK (((condition_role = 'limit'::annotation.condition_role) OR (num_nonnulls(measurement_method_concept_id, averaging_period_concept_id) = 0))),
    CONSTRAINT statement_condition_one_threshold_source CHECK ((num_nonnulls(threshold, delegate_provision_id, value_concept_id, delegate_permit_concept_id) = 1)),
    CONSTRAINT statement_condition_value_concept_is_equality CHECK (((value_concept_id IS NULL) OR (comparator = ANY (ARRAY['eq'::annotation.comparator, 'ne'::annotation.comparator]))))
);

COMMENT ON COLUMN annotation.statement_condition.needs_assessment IS '構成要件的不確定法律概念（情節重大、必要時）：系統不從事實判定它。效果面的裁量見 sanction.discretionary。';

COMMENT ON COLUMN annotation.statement_condition.delegate_permit_concept_id IS '委由個別處分：門檻值是該種許可所載、同一概念的量值，由呼叫端隨案件事實提供。';

COMMENT ON COLUMN annotation.statement_condition.measurement_method_concept_id IS '限值的檢測方法（如連續自動監測、人工採樣）；空值是未標註。';

COMMENT ON COLUMN annotation.statement_condition.averaging_period_concept_id IS '限值的平均時段（如即時值、小時平均值、日平均值、最大值）；空值是未標註。';

COMMENT ON COLUMN annotation.statement_condition.carried IS '承自上位：款、目的上位項，或半句話的句首，所寫的適用情形，由程式帶入而不是抽取者寫的。只能是成立與否的 scope 要件，概念名稱是原文那一段的字。';

ALTER TABLE annotation.statement_condition ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.statement_condition_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE annotation.statement_exception (
    exception_id bigint NOT NULL,
    excluded_id bigint NOT NULL,
    span int4range,
    CONSTRAINT statement_exception_check CHECK ((exception_id <> excluded_id))
);

CREATE VIEW annotation.statement_health AS
 SELECT (confirm_state)::text AS state,
    count(*) AS statements
   FROM annotation.assertion c
  WHERE (assertion_kind = 'statement'::annotation.assertion_kind)
  GROUP BY confirm_state;

COMMENT ON VIEW annotation.statement_health IS '各審核狀態的陳述數：extracted 待驗證、confirmed 可進推論、rejected 已駁回。';

CREATE TABLE annotation.statement_override (
    prevailing_id bigint NOT NULL,
    subordinate_id bigint NOT NULL,
    priority_basis annotation.priority_basis NOT NULL,
    CONSTRAINT statement_override_check CHECK ((prevailing_id <> subordinate_id))
);

CREATE TABLE annotation.statement_slot (
    statement_id bigint NOT NULL,
    slot_role annotation.slot_role NOT NULL,
    concept_id bigint,
    literal text,
    alternative smallint DEFAULT 1 NOT NULL,
    liability_basis annotation.liability_basis,
    unstated boolean DEFAULT false NOT NULL,
    CONSTRAINT statement_slot_alternative_check CHECK ((alternative >= 1)),
    CONSTRAINT statement_slot_has_value CHECK ((unstated OR (concept_id IS NOT NULL) OR (literal IS NOT NULL))),
    CONSTRAINT statement_slot_liability_basis_is_subject CHECK (((liability_basis IS NULL) OR (slot_role = 'subject'::annotation.slot_role))),
    CONSTRAINT statement_slot_unstated_is_bare CHECK (((NOT unstated) OR ((slot_role = 'subject'::annotation.slot_role) AND (concept_id IS NULL) AND (literal IS NULL) AND (liability_basis IS NULL))))
);

COMMENT ON COLUMN annotation.statement_slot.alternative IS '同一角色的第幾個值；同角色多個值彼此擇一（甲或乙），編號只為區分，不表示順序。';

COMMENT ON COLUMN annotation.statement_slot.unstated IS '主體槽位專用：原文沒有寫出義務人（被動式、主題句、代名詞）。沒有值，也沒有責任基礎；推論視為主體不受限制。與其他擇一的主體值互斥。';

CREATE TABLE annotation.term_definition (
    id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    span int4range,
    term_concept_id bigint NOT NULL,
    definition_text text NOT NULL,
    assertion_kind annotation.assertion_kind GENERATED ALWAYS AS ('definition'::annotation.assertion_kind) STORED,
    CONSTRAINT term_definition_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT term_definition_definition_text_check CHECK ((definition_text <> ''::text))
);

CREATE TABLE annotation.term_definition_member (
    definition_id bigint NOT NULL,
    member_concept_id bigint NOT NULL
);

ALTER TABLE annotation.verification ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME annotation.verification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE corpus.constitutional_ruling (
    id bigint NOT NULL,
    ruling_no text NOT NULL,
    ruling_kind corpus.ruling_kind NOT NULL,
    decided_on date NOT NULL,
    source_url text NOT NULL,
    excerpt text NOT NULL,
    CONSTRAINT constitutional_ruling_excerpt_check CHECK ((excerpt <> ''::text)),
    CONSTRAINT constitutional_ruling_ruling_no_check CHECK (((ruling_no = btrim(ruling_no)) AND (ruling_no <> ''::text))),
    CONSTRAINT constitutional_ruling_source_url_check CHECK ((source_url ~ '^https://'::text))
);

ALTER TABLE corpus.constitutional_ruling ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.constitutional_ruling_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE corpus.legal_expression ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.legal_expression_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE corpus.legal_resource ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.legal_resource_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE corpus.lifecycle_event (
    id bigint NOT NULL,
    resource_id bigint NOT NULL,
    event_kind corpus.event_kind NOT NULL,
    event_date date,
    document_no text,
    expression_id bigint,
    source_text text,
    CONSTRAINT lifecycle_event_dated_or_sourced CHECK (((event_date IS NOT NULL) OR (source_text IS NOT NULL))),
    CONSTRAINT lifecycle_event_document_no_check CHECK ((document_no <> ''::text)),
    CONSTRAINT lifecycle_event_expression_is_promulgation CHECK (((expression_id IS NULL) OR ((event_date IS NOT NULL) AND (event_kind = ANY (ARRAY['enactment'::corpus.event_kind, 'full_amendment'::corpus.event_kind, 'partial_amendment'::corpus.event_kind]))))),
    CONSTRAINT lifecycle_event_source_text_check CHECK ((source_text <> ''::text)),
    CONSTRAINT lifecycle_event_unknown_keeps_source CHECK (((event_kind <> 'unknown'::corpus.event_kind) OR (source_text IS NOT NULL)))
);

ALTER TABLE corpus.lifecycle_event ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.lifecycle_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE corpus.provision ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.provision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE corpus.provision_version ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.provision_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE corpus.ruling_invalidation (
    id bigint NOT NULL,
    ruling_id bigint NOT NULL,
    resource_id bigint NOT NULL,
    provision_id bigint NOT NULL,
    invalid_from date NOT NULL,
    partial boolean DEFAULT false NOT NULL,
    extractor_version integer,
    CONSTRAINT ruling_invalidation_extractor_version_check CHECK ((extractor_version > 0))
);

ALTER TABLE corpus.ruling_invalidation ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.ruling_invalidation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE corpus.ruling_scan (
    source_url text NOT NULL,
    content_sha256 bytea NOT NULL,
    extractor_version integer NOT NULL,
    scanned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ruling_scan_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT ruling_scan_source_url_check CHECK ((source_url ~ '^https://'::text))
);

CREATE TABLE corpus.structure_heading (
    id bigint NOT NULL,
    resource_id bigint NOT NULL,
    expression_id bigint NOT NULL,
    parent_id bigint,
    heading_kind corpus.heading_kind NOT NULL,
    heading_no text NOT NULL,
    title text NOT NULL,
    sort_key integer[] NOT NULL,
    CONSTRAINT structure_heading_heading_no_check CHECK ((heading_no <> ''::text)),
    CONSTRAINT structure_heading_sort_key_check CHECK ((cardinality(sort_key) > 0))
);

COMMENT ON TABLE corpus.structure_heading IS '條之上的結構章節（編、章、節、款、目），依附於特定公布版本並以 sort_key 與條文交錯排序。';

COMMENT ON COLUMN corpus.structure_heading.sort_key IS '與 corpus.provision.sort_key 共用的整數陣列排序鍵；置於所屬第一條之前。';

ALTER TABLE corpus.structure_heading ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME corpus.structure_heading_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE guidance.cessation (
    document_id bigint NOT NULL,
    ceased_on date NOT NULL,
    by_document_id bigint,
    cause guidance.cessation_cause NOT NULL,
    issued_on date NOT NULL,
    by_issued_on date,
    dropped_feed source.feed,
    dropped_key text,
    CONSTRAINT ceases_after_issue CHECK ((ceased_on >= issued_on)),
    CONSTRAINT ceases_after_successor CHECK ((ceased_on >= by_issued_on)),
    CONSTRAINT cessation_check CHECK ((by_document_id <> document_id)),
    CONSTRAINT cessation_check1 CHECK (((by_document_id IS NULL) = (by_issued_on IS NULL))),
    CONSTRAINT cessation_check2 CHECK (((dropped_key IS NULL) = (dropped_feed IS NULL))),
    CONSTRAINT cessation_dropped_feed_check CHECK ((dropped_feed = 'moenv_guidance'::source.feed)),
    CONSTRAINT successor_names_the_rule CHECK (((cause = 'successor'::guidance.cessation_cause) = (by_document_id IS NOT NULL))),
    CONSTRAINT taken_down_names_the_item CHECK (((cause = 'taken_down'::guidance.cessation_cause) = (dropped_key IS NOT NULL)))
);

COMMENT ON COLUMN guidance.cessation.issued_on IS '本件的發文日（外鍵抄來，供 CHECK 比較）。';

COMMENT ON COLUMN guidance.cessation.by_issued_on IS '後函的發文日（外鍵抄來，供 CHECK 比較）。';

CREATE TABLE guidance.document (
    id bigint NOT NULL,
    issuer text NOT NULL,
    document_no text NOT NULL,
    issued_on date NOT NULL,
    rule_kind guidance.rule_kind NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    content_sha256 bytea NOT NULL,
    published_in text,
    source_url text,
    derivation_id bigint NOT NULL,
    CONSTRAINT document_body_check CHECK ((body <> ''::text)),
    CONSTRAINT document_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT document_document_no_check CHECK ((document_no <> ''::text)),
    CONSTRAINT document_issuer_check CHECK ((issuer <> ''::text)),
    CONSTRAINT document_published_in_check CHECK ((published_in <> ''::text)),
    CONSTRAINT document_source_url_check CHECK ((source_url <> ''::text)),
    CONSTRAINT document_subject_check CHECK ((subject <> ''::text))
);

COMMENT ON COLUMN guidance.document.published_in IS '公報出處（行政程序法第 160 條第 2 項）。';

ALTER TABLE guidance.document ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME guidance.document_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE guidance.interpretation (
    document_id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    CONSTRAINT interpretation_content_sha256_check CHECK ((octet_length(content_sha256) = 32))
);

CREATE VIEW guidance.law_name AS
 WITH named AS (
         SELECT guidance.name_key(e.title) AS name,
            r.local_id AS pcode,
            false AS alias
           FROM (corpus.legal_expression e
             JOIN corpus.legal_resource r ON ((r.id = e.resource_id)))
          WHERE (r.id_scheme = 'moj_pcode'::corpus.id_scheme)
        UNION
         SELECT guidance.name_key(m.m[1]) AS name_key,
            r.local_id,
            true
           FROM ((corpus.lifecycle_event e
             JOIN corpus.legal_resource r ON ((r.id = e.resource_id)))
             CROSS JOIN LATERAL regexp_matches(guidance.name_key(e.source_text), '原名稱:([^;)]+);新名稱[:;]'::text, 'g'::text) m(m))
          WHERE ((r.id_scheme = 'moj_pcode'::corpus.id_scheme) AND (e.source_text ~~ '%原名稱%'::text))
        )
 SELECT name,
        CASE
            WHEN (count(DISTINCT pcode) = 1) THEN min(pcode)
            ELSE NULL::text
        END AS pcode,
    bool_and(alias) AS alias
   FROM named
  GROUP BY name;

COMMENT ON VIEW guidance.law_name IS '錨定的法規名稱：name 唯一；pcode 為空值表示這個名稱對到兩部以上法規（不錨定）；alias 為真表示只出現在沿革的更名紀錄（舊名）。';

CREATE TABLE judgment.anchor (
    decision_id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    basis judgment.version_basis NOT NULL,
    CONSTRAINT anchor_content_sha256_check CHECK ((octet_length(content_sha256) = 32))
);

CREATE TABLE judgment.decision (
    id bigint NOT NULL,
    jid text NOT NULL,
    decided_on date NOT NULL,
    instance judgment.instance NOT NULL,
    kind judgment.kind DEFAULT 'ordinary'::judgment.kind NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    content_sha256 bytea NOT NULL,
    source_url text,
    derivation_id bigint NOT NULL,
    CONSTRAINT decision_body_check CHECK ((body <> ''::text)),
    CONSTRAINT decision_check CHECK (((kind <> 'grand_chamber'::judgment.kind) OR (instance = 'supreme'::judgment.instance))),
    CONSTRAINT decision_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT decision_jid_check CHECK ((jid ~ '^[A-Z]*A,'::text)),
    CONSTRAINT decision_source_url_check CHECK ((source_url <> ''::text)),
    CONSTRAINT decision_title_check CHECK ((title <> ''::text))
);

COMMENT ON COLUMN judgment.decision.title IS '裁判案由（來源的 JTITLE）。';

COMMENT ON COLUMN judgment.decision.body IS '裁判全文（來源的 JFULL）。撤下時刪除整列。';

ALTER TABLE judgment.decision ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME judgment.decision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE judgment."fetch" (
    jid text NOT NULL,
    listed_on date NOT NULL,
    status judgment.fetch_status NOT NULL,
    last_error text,
    updated_on date DEFAULT CURRENT_DATE NOT NULL,
    CONSTRAINT fetch_jid_check CHECK ((jid <> ''::text)),
    CONSTRAINT fetch_last_error_check CHECK ((last_error <> ''::text))
);

COMMENT ON TABLE judgment."fetch" IS '司法院異動清單的取用進度。再出現的 jid 改回 pending，因為內容可能已改。';

CREATE TABLE judgment.takedown (
    jid text NOT NULL,
    detected_on date NOT NULL,
    CONSTRAINT takedown_jid_check CHECK ((jid <> ''::text))
);

COMMENT ON TABLE judgment.takedown IS '撤下的墓碑：只有 jid 與偵測日，不留裁判的任何內容。同一 jid 再出現時載入會移除墓碑。';

CREATE TABLE retrieval.passage (
    content_sha256 bytea NOT NULL,
    tokenizer text NOT NULL,
    tsv tsvector NOT NULL,
    model text,
    embedding public.vector(1024),
    CONSTRAINT passage_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT passage_embedded_by_model CHECK (((model IS NULL) = (embedding IS NULL))),
    CONSTRAINT passage_model_check CHECK ((model <> ''::text)),
    CONSTRAINT passage_tokenizer_check CHECK ((tokenizer <> ''::text))
);

COMMENT ON COLUMN retrieval.passage.tokenizer IS '產生 tsv 的分詞器識別碼（實作版本加詞典雜湊）；與服務端不符者不參與字面臂。';

COMMENT ON COLUMN retrieval.passage.model IS '產生 embedding 的嵌入模型識別碼；尚未嵌入為空值。';

COMMENT ON COLUMN retrieval.passage.embedding IS '段落的嵌入向量（1024 維，bge-m3）；換維度不同的模型要另一支遷移。';

CREATE TABLE retrieval.term_stat (
    tokenizer text NOT NULL,
    term text NOT NULL,
    doc_freq integer NOT NULL,
    CONSTRAINT term_stat_doc_freq_check CHECK ((doc_freq > 0)),
    CONSTRAINT term_stat_term_check CHECK ((term <> ''::text)),
    CONSTRAINT term_stat_tokenizer_check CHECK ((tokenizer <> ''::text))
);

COMMENT ON TABLE retrieval.term_stat IS '各詞出現在幾個段落（retrieval.passage 的列）；只含現行分詞器，由 retrieval.refresh_term_stat 整批重算。';

CREATE TABLE retrieval.term_stat_total (
    tokenizer text NOT NULL,
    passages bigint NOT NULL,
    CONSTRAINT term_stat_total_passages_check CHECK ((passages >= 0)),
    CONSTRAINT term_stat_total_tokenizer_check CHECK ((tokenizer <> ''::text))
);

COMMENT ON TABLE retrieval.term_stat_total IS '計算 term_stat 時該分詞器的段落總數：詞頻比例的分母。';

CREATE TABLE scoring.matrix_factor (
    id bigint NOT NULL,
    matrix_id bigint NOT NULL,
    factor_code text NOT NULL,
    factor_role scoring.factor_role NOT NULL,
    label_zho text NOT NULL,
    statutory_ref text NOT NULL,
    default_value numeric(18,3),
    min_value numeric(18,3),
    max_value numeric(18,3),
    needs_judgement boolean DEFAULT false NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    CONSTRAINT matrix_factor_check CHECK (((min_value IS NULL) OR (max_value IS NULL) OR (min_value <= max_value))),
    CONSTRAINT matrix_factor_factor_code_check CHECK ((factor_code ~ '^[a-zA-Z0-9_]+$'::text))
);

COMMENT ON TABLE scoring.matrix_factor IS '裁罰矩陣之各計算因子（點數項、A–E 權重因子、營建工程缺失記點項或固定金額項）。';

ALTER TABLE scoring.matrix_factor ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME scoring.matrix_factor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE scoring.penalty_matrix (
    id bigint NOT NULL,
    provision_id bigint NOT NULL,
    content_sha256 bytea NOT NULL,
    matrix_code text NOT NULL,
    law_title text NOT NULL,
    norm_kind text NOT NULL,
    formula_kind scoring.formula_kind NOT NULL,
    legal_effect annotation.legal_effect DEFAULT 'fine'::annotation.legal_effect NOT NULL,
    base_amount numeric(18,3),
    statutory_min numeric(18,3),
    statutory_max numeric(18,3),
    demerit_threshold numeric(18,3),
    rounding_mode text DEFAULT 'floor_ntd'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT penalty_matrix_check CHECK (((statutory_min IS NULL) OR (statutory_max IS NULL) OR (statutory_min <= statutory_max))),
    CONSTRAINT penalty_matrix_check1 CHECK (((formula_kind <> 'demerit_accumulation'::scoring.formula_kind) OR (demerit_threshold IS NOT NULL))),
    CONSTRAINT penalty_matrix_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT penalty_matrix_matrix_code_check CHECK ((matrix_code ~ '^[a-z0-9_]+$'::text)),
    CONSTRAINT penalty_matrix_norm_kind_check CHECK ((norm_kind = ANY (ARRAY['delegated_regulation'::text, 'discretion_standard'::text]))),
    CONSTRAINT penalty_matrix_rounding_mode_check CHECK ((rounding_mode = ANY (ARRAY['floor_ntd'::text, 'round_ntd'::text, 'ceil_ntd'::text, 'none'::text])))
);

COMMENT ON TABLE scoring.penalty_matrix IS '環境法裁罰準則（法規命令）與裁量基準／記點原則（行政規則）之結構化計算矩陣；以 provision_id 與條文雜湊雙重錨定。';

ALTER TABLE scoring.penalty_matrix ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME scoring.penalty_matrix_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE source.import_problem (
    id bigint NOT NULL,
    item_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    severity text NOT NULL,
    problem_code text NOT NULL,
    detail text DEFAULT ''::text NOT NULL,
    CONSTRAINT import_problem_code_check CHECK ((problem_code <> ''::text)),
    CONSTRAINT import_problem_severity_check CHECK ((severity = ANY (ARRAY['error'::text, 'warning'::text])))
);

ALTER TABLE source.import_problem ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME source.import_problem_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE source.item (
    id bigint NOT NULL,
    feed source.feed NOT NULL,
    key text NOT NULL,
    content_sha256 bytea NOT NULL,
    first_snapshot_id bigint NOT NULL,
    last_snapshot_id bigint NOT NULL,
    dropped_on date,
    imported_sha256 bytea,
    parser_version integer,
    missed_crawls smallint DEFAULT 0 NOT NULL,
    CONSTRAINT dropped_after_two_missed_crawls CHECK (((feed <> 'moenv_guidance'::source.feed) OR ((dropped_on IS NOT NULL) = (missed_crawls = 2)))),
    CONSTRAINT item_check CHECK (((feed = 'moenv_guidance'::source.feed) OR (missed_crawls = 0))),
    CONSTRAINT item_content_sha256_check CHECK ((octet_length(content_sha256) = 32)),
    CONSTRAINT item_import_state CHECK (((imported_sha256 IS NULL) = (parser_version IS NULL))),
    CONSTRAINT item_imported_sha256_check CHECK ((octet_length(imported_sha256) = 32)),
    CONSTRAINT item_key_check CHECK ((key <> ''::text)),
    CONSTRAINT item_missed_crawls_check CHECK (((missed_crawls >= 0) AND (missed_crawls <= 2)))
);

COMMENT ON COLUMN source.item.missed_crawls IS '爬取的全量來源（moenv_guidance）：連續幾次完整走訪沒有出現，2 即下架；其他來源恆為 0。';

ALTER TABLE source.item ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME source.item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE source.snapshot (
    id bigint NOT NULL,
    feed source.feed NOT NULL,
    data_date date NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    sha256 bytea NOT NULL,
    size_bytes bigint NOT NULL,
    location text NOT NULL,
    license_notice text NOT NULL,
    origin_name text,
    CONSTRAINT snapshot_license_notice_check CHECK ((license_notice <> ''::text)),
    CONSTRAINT snapshot_location_check CHECK ((location <> ''::text)),
    CONSTRAINT snapshot_origin_name_check CHECK ((origin_name <> ''::text)),
    CONSTRAINT snapshot_sha256_check CHECK ((octet_length(sha256) = 32)),
    CONSTRAINT snapshot_size_check CHECK ((size_bytes > 0))
);

ALTER TABLE source.snapshot ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME source.snapshot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE vocab.concept (
    id bigint NOT NULL,
    scheme_id bigint CONSTRAINT concept_scheme_id_not_null1 NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    canonical_unit text,
    CONSTRAINT concept_check CHECK (((code <> ''::text) AND (name <> ''::text)))
);

COMMENT ON COLUMN vocab.concept.canonical_unit IS '數值概念之標準量綱單位（如 mg/L、ug/L、ppm、mg/Nm3、dB(A)、m3/day、NTD、point、hour）。';

ALTER TABLE vocab.concept ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME vocab.concept_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE vocab.concept_label (
    id bigint NOT NULL,
    concept_id bigint NOT NULL,
    label text NOT NULL,
    label_kind vocab.label_kind CONSTRAINT concept_label_kind_not_null NOT NULL,
    CONSTRAINT concept_label_label_check CHECK ((label <> ''::text))
);

ALTER TABLE vocab.concept_label ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME vocab.concept_label_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

CREATE TABLE vocab.concept_match (
    concept_id bigint NOT NULL,
    external_system text NOT NULL,
    external_id text NOT NULL,
    CONSTRAINT concept_match_external_id_check CHECK (((external_id <> ''::text) AND (external_id = btrim(external_id)))),
    CONSTRAINT concept_match_external_system_check CHECK ((external_system ~ '^[a-z][a-z0-9_]*$'::text))
);

COMMENT ON TABLE vocab.concept_match IS '概念與外部識別碼（如 cas、行業統計分類）的等同；對到同一個外部識別碼的概念視為同一個概念。';

CREATE TABLE vocab.concept_relation (
    source_concept_id bigint NOT NULL,
    target_concept_id bigint NOT NULL,
    relation_kind vocab.concept_relation_kind NOT NULL,
    CONSTRAINT concept_relation_check CHECK ((source_concept_id <> target_concept_id))
);

COMMENT ON TABLE vocab.concept_relation IS '受控詞彙之間的法定上位（broader）或等同（exact_match）關係；跨法涵攝僅得依此表或 concept_match 成立，不得無據跨法混用。';

CREATE TABLE vocab.concept_scheme (
    id bigint NOT NULL,
    code text NOT NULL,
    title text NOT NULL,
    scope_resource_id bigint,
    CONSTRAINT concept_scheme_check CHECK (((code <> ''::text) AND (title <> ''::text)))
);

ALTER TABLE vocab.concept_scheme ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME vocab.concept_scheme_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);

ALTER TABLE ONLY annotation.assertion
    ADD CONSTRAINT assertion_id_assertion_kind_key UNIQUE (id, assertion_kind);

ALTER TABLE ONLY annotation.assertion
    ADD CONSTRAINT assertion_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.derivation
    ADD CONSTRAINT derivation_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.extraction_attempt
    ADD CONSTRAINT extraction_attempt_pkey PRIMARY KEY (provision_id, content_sha256, producer, producer_version, round);

ALTER TABLE ONLY annotation.held_penalty_link
    ADD CONSTRAINT held_penalty_link_pkey PRIMARY KEY (held_penalty_id, statement_id);

ALTER TABLE ONLY annotation.held_penalty
    ADD CONSTRAINT held_penalty_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.held_penalty
    ADD CONSTRAINT held_penalty_provision_id_content_sha256_fingerprint_key UNIQUE (provision_id, content_sha256, fingerprint);

ALTER TABLE ONLY annotation.mutatis_mutandis
    ADD CONSTRAINT mutatis_mutandis_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.mutatis_mutandis
    ADD CONSTRAINT mutatis_mutandis_source_statement_id_target_statement_id_key UNIQUE (source_statement_id, target_statement_id);

ALTER TABLE ONLY annotation.sanction
    ADD CONSTRAINT sanction_pkey PRIMARY KEY (statement_id);

ALTER TABLE ONLY annotation.sanction
    ADD CONSTRAINT sanction_statement_effect UNIQUE (statement_id, legal_effect);

ALTER TABLE ONLY annotation.sanction_trigger
    ADD CONSTRAINT sanction_trigger_pkey PRIMARY KEY (statement_id, trigger_statement_id);

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.statement_exception
    ADD CONSTRAINT statement_exception_pkey PRIMARY KEY (exception_id, excluded_id);

ALTER TABLE ONLY annotation.statement
    ADD CONSTRAINT statement_id_kind UNIQUE (id, statement_kind);

ALTER TABLE ONLY annotation.statement_override
    ADD CONSTRAINT statement_override_pkey PRIMARY KEY (prevailing_id, subordinate_id);

ALTER TABLE ONLY annotation.statement
    ADD CONSTRAINT statement_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.statement_slot
    ADD CONSTRAINT statement_slot_pkey PRIMARY KEY (statement_id, slot_role, alternative);

ALTER TABLE ONLY annotation.term_definition_member
    ADD CONSTRAINT term_definition_member_pkey PRIMARY KEY (definition_id, member_concept_id);

ALTER TABLE ONLY annotation.term_definition
    ADD CONSTRAINT term_definition_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.verification
    ADD CONSTRAINT verification_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotation.violation
    ADD CONSTRAINT violation_pkey PRIMARY KEY (provision_id, content_sha256, cited_path, violation_kind, rule_version);

ALTER TABLE ONLY corpus.constitutional_ruling
    ADD CONSTRAINT constitutional_ruling_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.constitutional_ruling
    ADD CONSTRAINT constitutional_ruling_ruling_no_key UNIQUE (ruling_no);

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_id_resource_language UNIQUE (id, resource_id, language);

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_id_resource_version_date UNIQUE (id, resource_id, version_date);

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_resource_id_language_text_period_key UNIQUE (resource_id, language, text_period WITHOUT OVERLAPS);

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_resource_id_language_version_date_key UNIQUE (resource_id, language, version_date);

ALTER TABLE ONLY corpus.legal_resource
    ADD CONSTRAINT legal_resource_jurisdiction_scheme_local_id_key UNIQUE (jurisdiction, id_scheme, local_id);

ALTER TABLE ONLY corpus.legal_resource
    ADD CONSTRAINT legal_resource_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.lifecycle_event
    ADD CONSTRAINT lifecycle_event_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.provision
    ADD CONSTRAINT provision_id_resource_id_key UNIQUE (id, resource_id);

ALTER TABLE ONLY corpus.provision
    ADD CONSTRAINT provision_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.provision
    ADD CONSTRAINT provision_resource_id_path_key UNIQUE (resource_id, path);

ALTER TABLE ONLY corpus.provision_version
    ADD CONSTRAINT provision_version_current_no_overlap EXCLUDE USING gist (provision_id WITH =, language WITH =, force_period WITH &&) WHERE ((superseded_at IS NULL));

ALTER TABLE ONLY corpus.provision_version
    ADD CONSTRAINT provision_version_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.ruling_invalidation
    ADD CONSTRAINT ruling_invalidation_pkey PRIMARY KEY (id);

ALTER TABLE ONLY corpus.ruling_invalidation
    ADD CONSTRAINT ruling_invalidation_ruling_id_provision_id_key UNIQUE (ruling_id, provision_id);

ALTER TABLE ONLY corpus.ruling_scan
    ADD CONSTRAINT ruling_scan_pkey PRIMARY KEY (source_url);

ALTER TABLE ONLY corpus.structure_heading
    ADD CONSTRAINT structure_heading_id_expression_id_key UNIQUE (id, expression_id);

ALTER TABLE ONLY corpus.structure_heading
    ADD CONSTRAINT structure_heading_pkey PRIMARY KEY (id);

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT cessation_pkey PRIMARY KEY (document_id);

ALTER TABLE ONLY guidance.document
    ADD CONSTRAINT document_id_document_no_key UNIQUE (id, document_no);

ALTER TABLE ONLY guidance.document
    ADD CONSTRAINT document_id_issued_on_key UNIQUE (id, issued_on);

ALTER TABLE ONLY guidance.document
    ADD CONSTRAINT document_issuer_document_no_key UNIQUE (issuer, document_no);

ALTER TABLE ONLY guidance.document
    ADD CONSTRAINT document_pkey PRIMARY KEY (id);

ALTER TABLE ONLY guidance.interpretation
    ADD CONSTRAINT interpretation_pkey PRIMARY KEY (document_id, provision_id, content_sha256);

ALTER TABLE ONLY judgment.anchor
    ADD CONSTRAINT anchor_pkey PRIMARY KEY (decision_id, provision_id, content_sha256);

ALTER TABLE ONLY judgment.decision
    ADD CONSTRAINT decision_jid_key UNIQUE (jid);

ALTER TABLE ONLY judgment.decision
    ADD CONSTRAINT decision_pkey PRIMARY KEY (id);

ALTER TABLE ONLY judgment."fetch"
    ADD CONSTRAINT fetch_pkey PRIMARY KEY (jid);

ALTER TABLE ONLY judgment.takedown
    ADD CONSTRAINT takedown_pkey PRIMARY KEY (jid);

ALTER TABLE ONLY retrieval.passage
    ADD CONSTRAINT passage_pkey PRIMARY KEY (content_sha256);

ALTER TABLE ONLY retrieval.term_stat
    ADD CONSTRAINT term_stat_pkey PRIMARY KEY (tokenizer, term);

ALTER TABLE ONLY retrieval.term_stat_total
    ADD CONSTRAINT term_stat_total_pkey PRIMARY KEY (tokenizer);

ALTER TABLE ONLY scoring.matrix_factor
    ADD CONSTRAINT matrix_factor_matrix_id_factor_code_key UNIQUE (matrix_id, factor_code);

ALTER TABLE ONLY scoring.matrix_factor
    ADD CONSTRAINT matrix_factor_pkey PRIMARY KEY (id);

ALTER TABLE ONLY scoring.penalty_matrix
    ADD CONSTRAINT penalty_matrix_matrix_code_key UNIQUE (matrix_code);

ALTER TABLE ONLY scoring.penalty_matrix
    ADD CONSTRAINT penalty_matrix_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source.import_problem
    ADD CONSTRAINT import_problem_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source.item
    ADD CONSTRAINT item_feed_key_dropped_on_key UNIQUE (feed, key, dropped_on);

ALTER TABLE ONLY source.item
    ADD CONSTRAINT item_feed_key_key UNIQUE (feed, key);

ALTER TABLE ONLY source.item
    ADD CONSTRAINT item_pkey PRIMARY KEY (id);

ALTER TABLE ONLY source.snapshot
    ADD CONSTRAINT snapshot_feed_sha256_key UNIQUE (feed, sha256);

ALTER TABLE ONLY source.snapshot
    ADD CONSTRAINT snapshot_pkey PRIMARY KEY (id);

ALTER TABLE ONLY vocab.concept_label
    ADD CONSTRAINT concept_label_concept_id_label_kind_key UNIQUE (concept_id, label, label_kind);

ALTER TABLE ONLY vocab.concept_label
    ADD CONSTRAINT concept_label_pkey PRIMARY KEY (id);

ALTER TABLE ONLY vocab.concept_match
    ADD CONSTRAINT concept_match_pkey PRIMARY KEY (concept_id, external_system);

ALTER TABLE ONLY vocab.concept
    ADD CONSTRAINT concept_pkey PRIMARY KEY (id);

ALTER TABLE ONLY vocab.concept_relation
    ADD CONSTRAINT concept_relation_pkey PRIMARY KEY (source_concept_id, target_concept_id, relation_kind);

ALTER TABLE ONLY vocab.concept_scheme
    ADD CONSTRAINT concept_scheme_code_key UNIQUE (code);

ALTER TABLE ONLY vocab.concept
    ADD CONSTRAINT concept_scheme_id_code_key UNIQUE (scheme_id, code);

ALTER TABLE ONLY vocab.concept_scheme
    ADD CONSTRAINT concept_scheme_pkey PRIMARY KEY (id);

CREATE INDEX assertion_by_derivation ON annotation.assertion USING btree (derivation_id);

CREATE INDEX assertion_extracted ON annotation.assertion USING btree (assertion_kind, id) WHERE (confirm_state = 'extracted'::annotation.confirm_state);

CREATE UNIQUE INDEX assertion_unique_content ON annotation.assertion USING btree (assertion_kind, fingerprint) WHERE (confirm_state <> 'rejected'::annotation.confirm_state);

CREATE INDEX delegation_by_command ON annotation.delegation USING btree (command_resource_id, command_provision_id);

CREATE INDEX delegation_by_law ON annotation.delegation USING btree (law_resource_id) WHERE (law_resource_id IS NOT NULL);

CREATE INDEX delegation_usable_law_cmd ON annotation.delegation USING btree (law_resource_id, command_resource_id) INCLUDE (command_provision_id, command_content_sha256, law_provision_id, cited_path) WHERE (law_resource_id IS NOT NULL);

CREATE INDEX held_penalty_by_anchor ON annotation.held_penalty USING btree (provision_id, content_sha256);

CREATE INDEX mutatis_mutandis_by_source ON annotation.mutatis_mutandis USING btree (source_statement_id);

CREATE INDEX sanction_by_punished ON annotation.sanction USING btree (punished_statement_id);

CREATE INDEX sanction_trigger_by_trigger ON annotation.sanction_trigger USING btree (trigger_statement_id);

CREATE INDEX statement_by_anchor ON annotation.statement USING btree (provision_id, content_sha256);

CREATE INDEX statement_condition_by_averaging ON annotation.statement_condition USING btree (averaging_period_concept_id) WHERE (averaging_period_concept_id IS NOT NULL);

CREATE INDEX statement_condition_by_concept ON annotation.statement_condition USING btree (concept_id);

CREATE INDEX statement_condition_by_delegate ON annotation.statement_condition USING btree (delegate_provision_id) WHERE (delegate_provision_id IS NOT NULL);

CREATE INDEX statement_condition_by_method ON annotation.statement_condition USING btree (measurement_method_concept_id) WHERE (measurement_method_concept_id IS NOT NULL);

CREATE INDEX statement_condition_by_permit ON annotation.statement_condition USING btree (delegate_permit_concept_id) WHERE (delegate_permit_concept_id IS NOT NULL);

CREATE INDEX statement_condition_by_statement ON annotation.statement_condition USING btree (statement_id);

CREATE INDEX statement_condition_by_value_concept ON annotation.statement_condition USING btree (value_concept_id) WHERE (value_concept_id IS NOT NULL);

CREATE INDEX statement_condition_limit_by_stmt_concept ON annotation.statement_condition USING btree (statement_id, concept_id) INCLUDE (threshold, measurement_method_concept_id, averaging_period_concept_id) WHERE ((condition_role = 'limit'::annotation.condition_role) AND (threshold IS NOT NULL));

CREATE INDEX statement_exception_by_excluded ON annotation.statement_exception USING btree (excluded_id);

CREATE INDEX statement_override_by_subordinate ON annotation.statement_override USING btree (subordinate_id);

CREATE INDEX statement_slot_by_concept ON annotation.statement_slot USING btree (concept_id) WHERE (concept_id IS NOT NULL);

CREATE INDEX term_definition_by_anchor ON annotation.term_definition USING btree (provision_id, content_sha256);

CREATE INDEX term_definition_by_term ON annotation.term_definition USING btree (term_concept_id);

CREATE INDEX term_definition_member_by_member ON annotation.term_definition_member USING btree (member_concept_id);

CREATE INDEX verification_by_assertion ON annotation.verification USING btree (assertion_id, method, id DESC);

CREATE INDEX violation_by_cited ON annotation.violation USING btree (cited_provision_id);

CREATE INDEX legal_expression_available_at_gist ON corpus.legal_expression USING gist (language, text_period) WHERE (text_status = 'available'::corpus.text_status);

CREATE INDEX legal_expression_available_by_resource ON corpus.legal_expression USING btree (resource_id, language) INCLUDE (title, text_period) WHERE (text_status = 'available'::corpus.text_status);

CREATE INDEX lifecycle_event_by_expression ON corpus.lifecycle_event USING btree (expression_id) WHERE (expression_id IS NOT NULL);

CREATE INDEX lifecycle_event_by_resource ON corpus.lifecycle_event USING btree (resource_id, event_kind);

CREATE INDEX lifecycle_event_enactment_by_resource ON corpus.lifecycle_event USING btree (resource_id, event_date) WHERE (event_kind = 'enactment'::corpus.event_kind);

CREATE INDEX provision_by_parent ON corpus.provision USING btree (parent_id) WHERE (parent_id IS NOT NULL);

CREATE INDEX provision_version_by_expression ON corpus.provision_version USING btree (expression_id);

CREATE INDEX provision_version_by_provision ON corpus.provision_version USING btree (provision_id);

CREATE INDEX provision_version_current_by_content ON corpus.provision_version USING btree (content_sha256) WHERE (superseded_at IS NULL);

CREATE INDEX provision_version_current_by_prov_sha_lang ON corpus.provision_version USING btree (provision_id, content_sha256, language) INCLUDE (force_period) WHERE (superseded_at IS NULL);

CREATE INDEX provision_version_current_by_resource_lang_gist ON corpus.provision_version USING gist (resource_id, language, force_period) WHERE (superseded_at IS NULL);

CREATE UNIQUE INDEX provision_version_current_per_expression ON corpus.provision_version USING btree (provision_id, expression_id) WHERE (superseded_at IS NULL);

CREATE INDEX ruling_invalidation_by_provision ON corpus.ruling_invalidation USING btree (provision_id);

CREATE INDEX structure_heading_by_expression ON corpus.structure_heading USING btree (expression_id, sort_key);

CREATE INDEX cessation_by_successor ON guidance.cessation USING btree (by_document_id) WHERE (by_document_id IS NOT NULL);

CREATE INDEX document_by_derivation ON guidance.document USING btree (derivation_id);

CREATE INDEX interpretation_by_anchor ON guidance.interpretation USING btree (provision_id, content_sha256);

CREATE INDEX anchor_by_provision ON judgment.anchor USING btree (provision_id, content_sha256);

CREATE INDEX decision_by_derivation ON judgment.decision USING btree (derivation_id);

CREATE INDEX fetch_pending ON judgment."fetch" USING btree (listed_on, jid) WHERE (status = 'pending'::judgment.fetch_status);

CREATE INDEX passage_embedding ON retrieval.passage USING hnsw (embedding public.vector_cosine_ops);

CREATE INDEX passage_tsv ON retrieval.passage USING gin (tsv);

CREATE INDEX penalty_matrix_by_provision ON scoring.penalty_matrix USING btree (provision_id);

CREATE INDEX import_problem_by_code ON source.import_problem USING btree (severity, problem_code);

CREATE INDEX import_problem_by_item ON source.import_problem USING btree (item_id);

CREATE INDEX import_problem_by_snapshot ON source.import_problem USING btree (snapshot_id);

CREATE INDEX item_by_first_snapshot ON source.item USING btree (first_snapshot_id);

CREATE INDEX item_by_last_snapshot ON source.item USING btree (last_snapshot_id);

CREATE INDEX snapshot_by_origin ON source.snapshot USING btree (feed, origin_name) WHERE (origin_name IS NOT NULL);

CREATE INDEX concept_match_by_external ON vocab.concept_match USING btree (external_system, external_id);

CREATE INDEX concept_relation_by_target ON vocab.concept_relation USING btree (target_concept_id, relation_kind);

CREATE INDEX concept_scheme_by_scope ON vocab.concept_scheme USING btree (scope_resource_id) WHERE (scope_resource_id IS NOT NULL);

CREATE TRIGGER assertion_loaded_settled AFTER INSERT ON annotation.assertion FOR EACH ROW WHEN ((new.confirm_state <> 'extracted'::annotation.confirm_state)) EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER assertion_loaded_settled_by_reviewer BEFORE INSERT ON annotation.assertion FOR EACH ROW EXECUTE FUNCTION annotation.loaded_settled_needs_reviewer();

CREATE CONSTRAINT TRIGGER assertion_sealed AFTER INSERT ON annotation.assertion DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION annotation.assertion_sealed_at_commit();

CREATE TRIGGER assertion_state_changed AFTER UPDATE OF confirm_state ON annotation.assertion FOR EACH ROW WHEN ((old.confirm_state IS DISTINCT FROM new.confirm_state)) EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER delegation_content_after_seal BEFORE INSERT OR UPDATE ON annotation.delegation FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');

CREATE TRIGGER delegation_drops_assertion AFTER DELETE ON annotation.delegation FOR EACH ROW EXECUTE FUNCTION annotation.drop_assertion();

CREATE TRIGGER sanction_content_after_seal BEFORE INSERT OR UPDATE ON annotation.sanction FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');

CREATE TRIGGER sanction_punishes_confirmed BEFORE INSERT OR UPDATE OF punished_statement_id ON annotation.sanction FOR EACH ROW EXECUTE FUNCTION annotation.sanction_punishes_confirmed();

CREATE TRIGGER statement_condition_content_after_seal BEFORE INSERT OR UPDATE ON annotation.statement_condition FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');

CREATE TRIGGER statement_content_after_seal BEFORE INSERT OR UPDATE ON annotation.statement FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');

CREATE TRIGGER statement_drops_assertion AFTER DELETE ON annotation.statement FOR EACH ROW EXECUTE FUNCTION annotation.drop_assertion();

CREATE TRIGGER statement_slot_content_after_seal BEFORE INSERT OR UPDATE ON annotation.statement_slot FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');

CREATE TRIGGER statement_slot_unstated_is_alone BEFORE INSERT OR UPDATE ON annotation.statement_slot FOR EACH ROW EXECUTE FUNCTION annotation.subject_unstated_is_alone();

CREATE TRIGGER term_definition_content_after_seal BEFORE INSERT OR UPDATE ON annotation.term_definition FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');

CREATE TRIGGER term_definition_drops_assertion AFTER DELETE ON annotation.term_definition FOR EACH ROW EXECUTE FUNCTION annotation.drop_assertion();

CREATE TRIGGER term_definition_in_own_law BEFORE INSERT OR UPDATE OF term_concept_id, provision_id ON annotation.term_definition FOR EACH ROW EXECUTE FUNCTION annotation.term_in_own_law();

CREATE TRIGGER term_definition_member_content_after_seal BEFORE INSERT OR UPDATE ON annotation.term_definition_member FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('definition_id');

CREATE TRIGGER verification_not_by_extractor BEFORE INSERT OR UPDATE OF assertion_id, method, verifier ON annotation.verification FOR EACH ROW EXECUTE FUNCTION annotation.verifier_is_not_extractor();

CREATE TRIGGER legal_resource_repeal_caps_force BEFORE UPDATE OF repealed_on ON corpus.legal_resource FOR EACH ROW EXECUTE FUNCTION corpus.repeal_caps_force();

CREATE TRIGGER provision_parent_path BEFORE INSERT OR UPDATE OF path, parent_id ON corpus.provision FOR EACH ROW EXECUTE FUNCTION corpus.provision_parent_path();

CREATE TRIGGER provision_version_expression_date BEFORE INSERT OR UPDATE OF expression_id ON corpus.provision_version FOR EACH ROW EXECUTE FUNCTION corpus.provision_version_expression_date();

CREATE TRIGGER provision_version_hash BEFORE INSERT OR UPDATE OF content ON corpus.provision_version FOR EACH ROW EXECUTE FUNCTION corpus.provision_version_hash();

CREATE TRIGGER provision_version_language BEFORE INSERT OR UPDATE OF expression_id ON corpus.provision_version FOR EACH ROW EXECUTE FUNCTION corpus.copy_version_language();

CREATE TRIGGER provision_version_within_repeal BEFORE INSERT OR UPDATE OF force_period, superseded_at, resource_id ON corpus.provision_version FOR EACH ROW EXECUTE FUNCTION corpus.force_within_repeal();

CREATE TRIGGER ruling_invalidation_dated BEFORE INSERT OR UPDATE ON corpus.ruling_invalidation FOR EACH ROW EXECUTE FUNCTION corpus.ruling_invalidation_dated();

CREATE TRIGGER document_hash BEFORE INSERT OR UPDATE OF subject, body ON guidance.document FOR EACH ROW EXECUTE FUNCTION guidance.document_hash();

CREATE TRIGGER document_loaded_by_people_or_rules BEFORE INSERT ON guidance.document FOR EACH ROW EXECUTE FUNCTION guidance.loaded_by_people_or_rules();

CREATE TRIGGER decision_hash BEFORE INSERT OR UPDATE OF title, body ON judgment.decision FOR EACH ROW EXECUTE FUNCTION judgment.decision_hash();

CREATE TRIGGER decision_loaded_by_people_or_rules BEFORE INSERT OR UPDATE OF derivation_id ON judgment.decision FOR EACH ROW EXECUTE FUNCTION judgment.loaded_by_people_or_rules();

CREATE TRIGGER concept_keeps_definitions AFTER UPDATE OF scheme_id ON vocab.concept FOR EACH ROW EXECUTE FUNCTION annotation.scope_keeps_definitions();

CREATE TRIGGER concept_scheme_keeps_definitions AFTER UPDATE OF scope_resource_id ON vocab.concept_scheme FOR EACH ROW EXECUTE FUNCTION annotation.scope_keeps_definitions();

ALTER TABLE ONLY annotation.assertion
    ADD CONSTRAINT assertion_derivation_id_fkey FOREIGN KEY (derivation_id) REFERENCES annotation.derivation(id);

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_command_provision FOREIGN KEY (command_provision_id, command_resource_id) REFERENCES corpus.provision(id, resource_id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_command_resource_id_fkey FOREIGN KEY (command_resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_is_assertion FOREIGN KEY (id, assertion_kind) REFERENCES annotation.assertion(id, assertion_kind) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_law_provision FOREIGN KEY (law_provision_id, law_resource_id) REFERENCES corpus.provision(id, resource_id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.delegation
    ADD CONSTRAINT delegation_law_resource_id_fkey FOREIGN KEY (law_resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.extraction_attempt
    ADD CONSTRAINT extraction_attempt_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.held_penalty_link
    ADD CONSTRAINT held_penalty_link_held_penalty_id_fkey FOREIGN KEY (held_penalty_id) REFERENCES annotation.held_penalty(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.held_penalty_link
    ADD CONSTRAINT held_penalty_link_statement_id_fkey FOREIGN KEY (statement_id) REFERENCES annotation.sanction(statement_id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.held_penalty
    ADD CONSTRAINT held_penalty_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.held_penalty
    ADD CONSTRAINT held_penalty_punished_statement_id_fkey FOREIGN KEY (punished_statement_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.mutatis_mutandis
    ADD CONSTRAINT mutatis_mutandis_source_statement_id_fkey FOREIGN KEY (source_statement_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.mutatis_mutandis
    ADD CONSTRAINT mutatis_mutandis_target_statement_id_fkey FOREIGN KEY (target_statement_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.sanction
    ADD CONSTRAINT sanction_punishes_prescriptive FOREIGN KEY (punished_statement_id, punished_statement_kind) REFERENCES annotation.statement(id, statement_kind) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.sanction
    ADD CONSTRAINT sanction_statement_is_sanction FOREIGN KEY (statement_id, statement_kind) REFERENCES annotation.statement(id, statement_kind) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.sanction_trigger
    ADD CONSTRAINT sanction_trigger_lapsed_is_order FOREIGN KEY (trigger_statement_id, trigger_effect) REFERENCES annotation.sanction(statement_id, legal_effect) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.sanction_trigger
    ADD CONSTRAINT sanction_trigger_statement_id_fkey FOREIGN KEY (statement_id) REFERENCES annotation.sanction(statement_id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.sanction_trigger
    ADD CONSTRAINT sanction_trigger_trigger_statement_id_fkey FOREIGN KEY (trigger_statement_id) REFERENCES annotation.sanction(statement_id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_averaging_period_concept_id_fkey FOREIGN KEY (averaging_period_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_delegate_permit_concept_id_fkey FOREIGN KEY (delegate_permit_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_delegate_provision_id_fkey FOREIGN KEY (delegate_provision_id) REFERENCES corpus.provision(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_measurement_method_concept_id_fkey FOREIGN KEY (measurement_method_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_statement_id_fkey FOREIGN KEY (statement_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_condition
    ADD CONSTRAINT statement_condition_value_concept_id_fkey FOREIGN KEY (value_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_exception
    ADD CONSTRAINT statement_exception_exception_id_fkey FOREIGN KEY (exception_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_exception
    ADD CONSTRAINT statement_exception_excluded_id_fkey FOREIGN KEY (excluded_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement
    ADD CONSTRAINT statement_is_assertion FOREIGN KEY (id, assertion_kind) REFERENCES annotation.assertion(id, assertion_kind) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_override
    ADD CONSTRAINT statement_override_prevailing_id_fkey FOREIGN KEY (prevailing_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_override
    ADD CONSTRAINT statement_override_subordinate_id_fkey FOREIGN KEY (subordinate_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement
    ADD CONSTRAINT statement_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.statement_slot
    ADD CONSTRAINT statement_slot_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.statement_slot
    ADD CONSTRAINT statement_slot_statement_id_fkey FOREIGN KEY (statement_id) REFERENCES annotation.statement(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.term_definition
    ADD CONSTRAINT term_definition_is_assertion FOREIGN KEY (id, assertion_kind) REFERENCES annotation.assertion(id, assertion_kind) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.term_definition_member
    ADD CONSTRAINT term_definition_member_definition_id_fkey FOREIGN KEY (definition_id) REFERENCES annotation.term_definition(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.term_definition_member
    ADD CONSTRAINT term_definition_member_member_concept_id_fkey FOREIGN KEY (member_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.term_definition
    ADD CONSTRAINT term_definition_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.term_definition
    ADD CONSTRAINT term_definition_term_concept_id_fkey FOREIGN KEY (term_concept_id) REFERENCES vocab.concept(id);

ALTER TABLE ONLY annotation.verification
    ADD CONSTRAINT verification_assertion_id_fkey FOREIGN KEY (assertion_id) REFERENCES annotation.assertion(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.violation
    ADD CONSTRAINT violation_cited_provision_id_fkey FOREIGN KEY (cited_provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY annotation.violation
    ADD CONSTRAINT violation_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.legal_expression
    ADD CONSTRAINT legal_expression_text_continues FOREIGN KEY (resource_id, language, text_until) REFERENCES corpus.legal_expression(resource_id, language, version_date) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ONLY corpus.lifecycle_event
    ADD CONSTRAINT lifecycle_event_matches_expression FOREIGN KEY (expression_id, resource_id, event_date) REFERENCES corpus.legal_expression(id, resource_id, version_date) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.lifecycle_event
    ADD CONSTRAINT lifecycle_event_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.provision
    ADD CONSTRAINT provision_parent_id_resource_id_fkey FOREIGN KEY (parent_id, resource_id) REFERENCES corpus.provision(id, resource_id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.provision
    ADD CONSTRAINT provision_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.provision_version
    ADD CONSTRAINT provision_version_expression_date FOREIGN KEY (expression_id, resource_id, expression_date) REFERENCES corpus.legal_expression(id, resource_id, version_date) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.provision_version
    ADD CONSTRAINT provision_version_expression_language FOREIGN KEY (expression_id, resource_id, language) REFERENCES corpus.legal_expression(id, resource_id, language) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.provision_version
    ADD CONSTRAINT provision_version_provision_id_resource_id_fkey FOREIGN KEY (provision_id, resource_id) REFERENCES corpus.provision(id, resource_id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.ruling_invalidation
    ADD CONSTRAINT ruling_invalidation_provision_id_resource_id_fkey FOREIGN KEY (provision_id, resource_id) REFERENCES corpus.provision(id, resource_id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.ruling_invalidation
    ADD CONSTRAINT ruling_invalidation_ruling_id_fkey FOREIGN KEY (ruling_id) REFERENCES corpus.constitutional_ruling(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.structure_heading
    ADD CONSTRAINT structure_heading_expression_id_fkey FOREIGN KEY (expression_id) REFERENCES corpus.legal_expression(id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.structure_heading
    ADD CONSTRAINT structure_heading_parent_id_expression_id_fkey FOREIGN KEY (parent_id, expression_id) REFERENCES corpus.structure_heading(id, expression_id) ON DELETE CASCADE;

ALTER TABLE ONLY corpus.structure_heading
    ADD CONSTRAINT structure_heading_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT cessation_by_document_id_by_issued_on_fkey FOREIGN KEY (by_document_id, by_issued_on) REFERENCES guidance.document(id, issued_on) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT cessation_document_id_dropped_key_fkey FOREIGN KEY (document_id, dropped_key) REFERENCES guidance.document(id, document_no) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT cessation_document_id_fkey FOREIGN KEY (document_id) REFERENCES guidance.document(id) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT cessation_document_id_issued_on_fkey FOREIGN KEY (document_id, issued_on) REFERENCES guidance.document(id, issued_on) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.document
    ADD CONSTRAINT document_derivation_id_fkey FOREIGN KEY (derivation_id) REFERENCES annotation.derivation(id);

ALTER TABLE ONLY guidance.interpretation
    ADD CONSTRAINT interpretation_document_id_fkey FOREIGN KEY (document_id) REFERENCES guidance.document(id) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.interpretation
    ADD CONSTRAINT interpretation_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY guidance.cessation
    ADD CONSTRAINT taken_down_on_drop FOREIGN KEY (dropped_feed, dropped_key, ceased_on) REFERENCES source.item(feed, key, dropped_on);

ALTER TABLE ONLY judgment.anchor
    ADD CONSTRAINT anchor_decision_id_fkey FOREIGN KEY (decision_id) REFERENCES judgment.decision(id) ON DELETE CASCADE;

ALTER TABLE ONLY judgment.anchor
    ADD CONSTRAINT anchor_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY judgment.decision
    ADD CONSTRAINT decision_derivation_id_fkey FOREIGN KEY (derivation_id) REFERENCES annotation.derivation(id);

ALTER TABLE ONLY scoring.matrix_factor
    ADD CONSTRAINT matrix_factor_matrix_id_fkey FOREIGN KEY (matrix_id) REFERENCES scoring.penalty_matrix(id) ON DELETE CASCADE;

ALTER TABLE ONLY scoring.penalty_matrix
    ADD CONSTRAINT penalty_matrix_provision_id_fkey FOREIGN KEY (provision_id) REFERENCES corpus.provision(id) ON DELETE CASCADE;

ALTER TABLE ONLY source.import_problem
    ADD CONSTRAINT import_problem_item_id_fkey FOREIGN KEY (item_id) REFERENCES source.item(id) ON DELETE CASCADE;

ALTER TABLE ONLY source.import_problem
    ADD CONSTRAINT import_problem_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES source.snapshot(id) ON DELETE CASCADE;

ALTER TABLE ONLY source.item
    ADD CONSTRAINT item_first_snapshot_id_fkey FOREIGN KEY (first_snapshot_id) REFERENCES source.snapshot(id);

ALTER TABLE ONLY source.item
    ADD CONSTRAINT item_last_snapshot_id_fkey FOREIGN KEY (last_snapshot_id) REFERENCES source.snapshot(id);

ALTER TABLE ONLY vocab.concept_label
    ADD CONSTRAINT concept_label_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES vocab.concept(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocab.concept_match
    ADD CONSTRAINT concept_match_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES vocab.concept(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocab.concept_relation
    ADD CONSTRAINT concept_relation_source_concept_id_fkey FOREIGN KEY (source_concept_id) REFERENCES vocab.concept(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocab.concept_relation
    ADD CONSTRAINT concept_relation_target_concept_id_fkey FOREIGN KEY (target_concept_id) REFERENCES vocab.concept(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocab.concept
    ADD CONSTRAINT concept_scheme_id_fkey FOREIGN KEY (scheme_id) REFERENCES vocab.concept_scheme(id) ON DELETE CASCADE;

ALTER TABLE ONLY vocab.concept_scheme
    ADD CONSTRAINT concept_scheme_scope_resource_id_fkey FOREIGN KEY (scope_resource_id) REFERENCES corpus.legal_resource(id) ON DELETE CASCADE;

GRANT USAGE ON SCHEMA annotation TO canon_reader;
GRANT USAGE ON SCHEMA annotation TO canon_writer;
GRANT USAGE ON SCHEMA annotation TO canon_reviewer;

GRANT USAGE ON SCHEMA corpus TO canon_reader;
GRANT USAGE ON SCHEMA corpus TO canon_writer;
GRANT USAGE ON SCHEMA corpus TO canon_reviewer;

GRANT USAGE ON SCHEMA guidance TO canon_reader;
GRANT USAGE ON SCHEMA guidance TO canon_writer;
GRANT USAGE ON SCHEMA guidance TO canon_reviewer;

GRANT USAGE ON SCHEMA judgment TO canon_reader;
GRANT USAGE ON SCHEMA judgment TO canon_writer;
GRANT USAGE ON SCHEMA judgment TO canon_reviewer;

GRANT USAGE ON SCHEMA retrieval TO canon_reader;
GRANT USAGE ON SCHEMA retrieval TO canon_writer;
GRANT USAGE ON SCHEMA retrieval TO canon_reviewer;

GRANT USAGE ON SCHEMA scoring TO canon_reader;
GRANT USAGE ON SCHEMA scoring TO canon_writer;
GRANT USAGE ON SCHEMA scoring TO canon_reviewer;

GRANT USAGE ON SCHEMA source TO canon_reader;
GRANT USAGE ON SCHEMA source TO canon_writer;
GRANT USAGE ON SCHEMA source TO canon_reviewer;

GRANT USAGE ON SCHEMA vocab TO canon_reader;
GRANT USAGE ON SCHEMA vocab TO canon_writer;
GRANT USAGE ON SCHEMA vocab TO canon_reviewer;

GRANT ALL ON FUNCTION annotation.penalty_candidates(p_held bigint) TO canon_reader;
GRANT ALL ON FUNCTION annotation.penalty_candidates(p_held bigint) TO canon_writer;
GRANT ALL ON FUNCTION annotation.penalty_candidates(p_held bigint) TO canon_reviewer;

REVOKE ALL ON FUNCTION annotation.record_verification(p_assertion bigint, p_method text, p_verifier text, p_result annotation.gate_result, p_evidence jsonb) FROM PUBLIC;
GRANT ALL ON FUNCTION annotation.record_verification(p_assertion bigint, p_method text, p_verifier text, p_result annotation.gate_result, p_evidence jsonb) TO canon_writer;
GRANT ALL ON FUNCTION annotation.record_verification(p_assertion bigint, p_method text, p_verifier text, p_result annotation.gate_result, p_evidence jsonb) TO canon_reviewer;

REVOKE ALL ON FUNCTION annotation.seal(p_assertion bigint) FROM PUBLIC;
GRANT ALL ON FUNCTION annotation.seal(p_assertion bigint) TO canon_writer;
GRANT ALL ON FUNCTION annotation.seal(p_assertion bigint) TO canon_reviewer;

REVOKE ALL ON FUNCTION annotation.set_confirm_state(p_assertion bigint, p_state annotation.confirm_state, p_reason text) FROM PUBLIC;
GRANT ALL ON FUNCTION annotation.set_confirm_state(p_assertion bigint, p_state annotation.confirm_state, p_reason text) TO canon_reviewer;

REVOKE ALL ON FUNCTION annotation.settle(p_kind annotation.assertion_kind, p_required text[]) FROM PUBLIC;
GRANT ALL ON FUNCTION annotation.settle(p_kind annotation.assertion_kind, p_required text[]) TO canon_writer;
GRANT ALL ON FUNCTION annotation.settle(p_kind annotation.assertion_kind, p_required text[]) TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement TO canon_reviewer;

GRANT SELECT ON TABLE corpus.provision TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.provision TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.provision TO canon_reviewer;

GRANT SELECT ON TABLE corpus.provision_version TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.provision_version TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.provision_version TO canon_reviewer;

GRANT SELECT ON TABLE corpus.legal_expression TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.legal_expression TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.legal_expression TO canon_reviewer;

GRANT SELECT ON TABLE corpus.legal_resource TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.legal_resource TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.legal_resource TO canon_reviewer;

GRANT ALL ON FUNCTION guidance.settle_crawl(p_seen text[], p_on date, p_source text) TO canon_writer;
GRANT ALL ON FUNCTION guidance.settle_crawl(p_seen text[], p_on date, p_source text) TO canon_reviewer;

GRANT SELECT ON TABLE annotation.assertion TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.assertion TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.assertion TO canon_reviewer;

GRANT USAGE ON SEQUENCE annotation.assertion_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.assertion_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE annotation.violation TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.violation TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.violation TO canon_reviewer;

GRANT SELECT ON TABLE annotation.current_violation TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.current_violation TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.current_violation TO canon_reviewer;

GRANT SELECT ON TABLE annotation.delegation TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.delegation TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.delegation TO canon_reviewer;

GRANT SELECT ON TABLE annotation.derivation TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.derivation TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.derivation TO canon_reviewer;

GRANT SELECT ON TABLE annotation.delegation_usable TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.delegation_usable TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.delegation_usable TO canon_reviewer;

GRANT USAGE ON SEQUENCE annotation.derivation_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.derivation_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE annotation.duty_gap TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.duty_gap TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.duty_gap TO canon_reviewer;

GRANT SELECT ON TABLE annotation.extraction_attempt TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.extraction_attempt TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.extraction_attempt TO canon_reviewer;

GRANT SELECT ON TABLE annotation.verification TO canon_reader;
GRANT SELECT ON TABLE annotation.verification TO canon_writer;
GRANT SELECT ON TABLE annotation.verification TO canon_reviewer;

GRANT SELECT ON TABLE annotation.gate_health TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.gate_health TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.gate_health TO canon_reviewer;

GRANT SELECT ON TABLE annotation.held_penalty TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty TO canon_reviewer;

GRANT USAGE ON SEQUENCE annotation.held_penalty_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.held_penalty_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE annotation.held_penalty_link TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty_link TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty_link TO canon_reviewer;

GRANT SELECT ON TABLE annotation.held_penalty_state TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty_state TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.held_penalty_state TO canon_reviewer;

GRANT SELECT ON TABLE annotation.mutatis_mutandis TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE annotation.mutatis_mutandis TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.mutatis_mutandis TO canon_reviewer;

GRANT SELECT,USAGE ON SEQUENCE annotation.mutatis_mutandis_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.mutatis_mutandis_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE annotation.unresolved_duty TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.unresolved_duty TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.unresolved_duty TO canon_reviewer;

GRANT SELECT ON TABLE annotation.reextraction_due TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.reextraction_due TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.reextraction_due TO canon_reviewer;

GRANT SELECT ON TABLE annotation.sanction TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.sanction TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.sanction TO canon_reviewer;

GRANT SELECT ON TABLE annotation.sanction_trigger TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.sanction_trigger TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.sanction_trigger TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement_condition TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement_condition TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement_condition TO canon_reviewer;

GRANT USAGE ON SEQUENCE annotation.statement_condition_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.statement_condition_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement_exception TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement_exception TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement_exception TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement_health TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement_health TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement_health TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement_override TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement_override TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement_override TO canon_reviewer;

GRANT SELECT ON TABLE annotation.statement_slot TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.statement_slot TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.statement_slot TO canon_reviewer;

GRANT SELECT ON TABLE annotation.term_definition TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.term_definition TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.term_definition TO canon_reviewer;

GRANT SELECT ON TABLE annotation.term_definition_member TO canon_reader;
GRANT SELECT,INSERT ON TABLE annotation.term_definition_member TO canon_writer;
GRANT SELECT,INSERT ON TABLE annotation.term_definition_member TO canon_reviewer;

GRANT USAGE ON SEQUENCE annotation.verification_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE annotation.verification_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE corpus.constitutional_ruling TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.constitutional_ruling TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.constitutional_ruling TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.constitutional_ruling_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.constitutional_ruling_id_seq TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.legal_expression_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.legal_expression_id_seq TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.legal_resource_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.legal_resource_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE corpus.lifecycle_event TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.lifecycle_event TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.lifecycle_event TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.lifecycle_event_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.lifecycle_event_id_seq TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.provision_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.provision_id_seq TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.provision_version_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.provision_version_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE corpus.ruling_invalidation TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.ruling_invalidation TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.ruling_invalidation TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.ruling_invalidation_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.ruling_invalidation_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE corpus.ruling_scan TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.ruling_scan TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.ruling_scan TO canon_reviewer;

GRANT SELECT ON TABLE corpus.structure_heading TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.structure_heading TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE corpus.structure_heading TO canon_reviewer;

GRANT USAGE ON SEQUENCE corpus.structure_heading_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE corpus.structure_heading_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE guidance.cessation TO canon_reader;
GRANT SELECT,INSERT ON TABLE guidance.cessation TO canon_writer;
GRANT SELECT,INSERT ON TABLE guidance.cessation TO canon_reviewer;

GRANT SELECT ON TABLE guidance.document TO canon_reader;
GRANT SELECT,INSERT ON TABLE guidance.document TO canon_writer;
GRANT SELECT,INSERT ON TABLE guidance.document TO canon_reviewer;

GRANT USAGE ON SEQUENCE guidance.document_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE guidance.document_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE guidance.interpretation TO canon_reader;
GRANT SELECT,INSERT ON TABLE guidance.interpretation TO canon_writer;
GRANT SELECT,INSERT ON TABLE guidance.interpretation TO canon_reviewer;

GRANT SELECT ON TABLE guidance.law_name TO canon_reader;
GRANT SELECT,INSERT ON TABLE guidance.law_name TO canon_writer;
GRANT SELECT,INSERT ON TABLE guidance.law_name TO canon_reviewer;

GRANT SELECT ON TABLE judgment.anchor TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE judgment.anchor TO canon_writer;
GRANT SELECT,INSERT ON TABLE judgment.anchor TO canon_reviewer;

GRANT SELECT ON TABLE judgment.decision TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE judgment.decision TO canon_writer;
GRANT SELECT,INSERT ON TABLE judgment.decision TO canon_reviewer;

GRANT USAGE ON SEQUENCE judgment.decision_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE judgment.decision_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE judgment."fetch" TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE judgment."fetch" TO canon_writer;
GRANT SELECT,INSERT ON TABLE judgment."fetch" TO canon_reviewer;

GRANT SELECT ON TABLE judgment.takedown TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE judgment.takedown TO canon_writer;
GRANT SELECT,INSERT ON TABLE judgment.takedown TO canon_reviewer;

GRANT SELECT ON TABLE retrieval.passage TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.passage TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.passage TO canon_reviewer;

GRANT SELECT ON TABLE retrieval.term_stat TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.term_stat TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.term_stat TO canon_reviewer;

GRANT SELECT ON TABLE retrieval.term_stat_total TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.term_stat_total TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE retrieval.term_stat_total TO canon_reviewer;

GRANT SELECT ON TABLE scoring.matrix_factor TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE scoring.matrix_factor TO canon_writer;
GRANT SELECT ON TABLE scoring.matrix_factor TO canon_reviewer;

GRANT SELECT,USAGE ON SEQUENCE scoring.matrix_factor_id_seq TO canon_writer;

GRANT SELECT ON TABLE scoring.penalty_matrix TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE scoring.penalty_matrix TO canon_writer;
GRANT SELECT ON TABLE scoring.penalty_matrix TO canon_reviewer;

GRANT SELECT,USAGE ON SEQUENCE scoring.penalty_matrix_id_seq TO canon_writer;

GRANT SELECT ON TABLE source.import_problem TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.import_problem TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.import_problem TO canon_reviewer;

GRANT USAGE ON SEQUENCE source.import_problem_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE source.import_problem_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE source.item TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.item TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.item TO canon_reviewer;

GRANT USAGE ON SEQUENCE source.item_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE source.item_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE source.snapshot TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.snapshot TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE source.snapshot TO canon_reviewer;

GRANT USAGE ON SEQUENCE source.snapshot_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE source.snapshot_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE vocab.concept TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept TO canon_reviewer;

GRANT USAGE ON SEQUENCE vocab.concept_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE vocab.concept_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE vocab.concept_label TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_label TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_label TO canon_reviewer;

GRANT USAGE ON SEQUENCE vocab.concept_label_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE vocab.concept_label_id_seq TO canon_reviewer;

GRANT SELECT ON TABLE vocab.concept_match TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_match TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_match TO canon_reviewer;

GRANT SELECT ON TABLE vocab.concept_relation TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_relation TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_relation TO canon_reviewer;

GRANT SELECT ON TABLE vocab.concept_scheme TO canon_reader;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_scheme TO canon_writer;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE vocab.concept_scheme TO canon_reviewer;

GRANT USAGE ON SEQUENCE vocab.concept_scheme_id_seq TO canon_writer;
GRANT USAGE ON SEQUENCE vocab.concept_scheme_id_seq TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA annotation GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA annotation GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA annotation GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA annotation GRANT SELECT,INSERT ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA annotation GRANT SELECT,INSERT ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA corpus GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA corpus GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA corpus GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA corpus GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA corpus GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA guidance GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA guidance GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance GRANT SELECT,INSERT ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance GRANT SELECT,INSERT ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA judgment GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA judgment GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment GRANT SELECT,INSERT ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA retrieval GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA retrieval GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA retrieval GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA retrieval GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA retrieval GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA source GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA source GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA source GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA source GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA source GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT USAGE ON SEQUENCES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT USAGE ON SEQUENCES TO canon_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT SELECT ON TABLES TO canon_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO canon_reviewer;

-- 交易內的 search_path 還原，migrate 接著寫 schema_migration 時才找得到它。
RESET search_path;
