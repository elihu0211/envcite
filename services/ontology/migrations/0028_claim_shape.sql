-- 0028_claim_shape — 主張的內容封存與去重、槽位可列舉擇一、用語方案範圍雙向守住
--
-- - 同一份標註載入兩次、或兩個抽取者抽出相同的陳述，原本會留下兩個主張，推論把它們當兩條。
--   主張寫完內容後由 annotation.seal 以內容算出指紋並封存：已有相同種類、相同指紋、未駁回的主張時，
--   丟掉新寫的這份、回傳既有的 id；封存之後不能再補內容。指紋由資料庫依已寫入的列計算，
--   不靠呼叫端；範圍（span）與引文只是定位，不算內容。駁回的主張不擋相同內容重新載入（修正的路徑）。
--   每個新主張都必須在同一交易內封存（延遲檢查）。
-- - 指紋涵蓋：陳述的錨定、種類、方向、槽位、要件與罰則（含所處罰的陳述）；用語定義的錨定、用語與成員。
--   例外與優先關係是陳述之間的關係，不屬於任何一方的內容。
-- - 既有主張補算指紋；未駁回者之間內容重複的，保留最早的一個，其餘駁回並記一列 policy。
-- - 槽位可列舉（「事業或其負責人」）：同一角色多個值，彼此是擇一（或）。
-- - 0025 只在新增或修改定義時檢查用語方案的範圍；概念換方案、方案改範圍也要守住。

-- 槽位擇一

ALTER TABLE annotation.statement_slot
    ADD COLUMN alternative smallint NOT NULL DEFAULT 1 CHECK (alternative >= 1),
    DROP CONSTRAINT statement_slot_pkey,
    ADD CONSTRAINT statement_slot_pkey PRIMARY KEY (statement_id, slot_role, alternative);

COMMENT ON COLUMN annotation.statement_slot.alternative IS
    '同一角色的第幾個值；同角色多個值彼此擇一（甲或乙），編號只為區分，不表示順序。';

-- 指紋

ALTER TABLE annotation.claim
    ADD COLUMN fingerprint bytea CHECK (octet_length(fingerprint) = 32);

COMMENT ON COLUMN annotation.claim.fingerprint IS
    '內容指紋（annotation.claim_fingerprint）；由 annotation.seal 寫入，寫入後內容不可再補。';

CREATE FUNCTION annotation.claim_fingerprint(p_claim bigint) RETURNS bytea
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT sha256(convert_to(
        CASE c.claim_kind
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
      FROM annotation.claim AS c
     WHERE c.id = p_claim
$$;

UPDATE annotation.claim SET fingerprint = annotation.claim_fingerprint(id);

-- 既有的重複：保留最早的，其餘駁回（policy 列先寫，觸發器就不再補記）

WITH dup AS (
    SELECT id, first_value(id) OVER (PARTITION BY claim_kind, fingerprint ORDER BY id) AS kept
      FROM annotation.claim
     WHERE confirm_state <> 'rejected'
)
INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
SELECT id, 'policy', 'migration:0028', 'fail',
       jsonb_build_object('action', 'rejected', 'reason', 'duplicate', 'kept', kept)
  FROM dup WHERE id <> kept;

UPDATE annotation.claim AS c SET confirm_state = 'rejected'
  FROM annotation.verification AS v
 WHERE v.claim_id = c.id AND v.verifier = 'migration:0028';

CREATE UNIQUE INDEX claim_unique_content ON annotation.claim (claim_kind, fingerprint)
    WHERE confirm_state <> 'rejected';

-- 封存

CREATE FUNCTION annotation.seal(p_claim bigint) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_kind     annotation.claim_kind;
    v_sealed   bytea;
    v_print    bytea;
    v_existing bigint;
BEGIN
    SELECT claim_kind, fingerprint INTO v_kind, v_sealed FROM annotation.claim WHERE id = p_claim;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such claim: %', p_claim USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_sealed IS NOT NULL THEN
        RAISE EXCEPTION 'claim % is already sealed', p_claim USING ERRCODE = 'check_violation';
    END IF;
    v_print := annotation.claim_fingerprint(p_claim);
    IF v_print IS NULL THEN
        RAISE EXCEPTION 'claim % has no subtype row to seal', p_claim USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO v_existing FROM annotation.claim
     WHERE claim_kind = v_kind AND fingerprint = v_print AND confirm_state <> 'rejected';
    IF FOUND THEN
        DELETE FROM annotation.claim WHERE id = p_claim;
        RETURN v_existing;
    END IF;
    UPDATE annotation.claim SET fingerprint = v_print WHERE id = p_claim;
    RETURN p_claim;
END
$$;

REVOKE EXECUTE ON FUNCTION annotation.seal(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION annotation.seal(bigint)
    TO envcite_ontology_writer, envcite_ontology_reviewer;

CREATE FUNCTION annotation.claim_sealed_at_commit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.claim WHERE id = NEW.id AND fingerprint IS NULL) THEN
        RAISE EXCEPTION 'claim % was written but not sealed', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER claim_sealed
    AFTER INSERT ON annotation.claim
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION annotation.claim_sealed_at_commit();

-- 封存之後不能再補內容

CREATE FUNCTION annotation.content_after_seal() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    -- 觸發器參數是指向主張的欄名：各表不同，NEW 上不存在的欄位不能寫進同一個運算式
    v_claim bigint := (to_jsonb(NEW) ->> TG_ARGV[0])::bigint;
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.claim WHERE id = v_claim AND fingerprint IS NOT NULL) THEN
        RAISE EXCEPTION 'claim % is sealed; its content cannot change', v_claim
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER statement_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.statement
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');
CREATE TRIGGER statement_slot_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.statement_slot
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');
CREATE TRIGGER statement_condition_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.statement_condition
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');
CREATE TRIGGER sanction_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.sanction
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('statement_id');
CREATE TRIGGER term_definition_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.term_definition
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');
CREATE TRIGGER term_definition_member_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.term_definition_member
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('definition_id');

-- 用語方案範圍：概念換方案、方案改範圍時也守住

CREATE FUNCTION annotation.scope_keeps_definitions() RETURNS trigger
LANGUAGE plpgsql AS $$
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

CREATE TRIGGER concept_keeps_definitions
    AFTER UPDATE OF scheme_id ON vocab.concept
    FOR EACH ROW EXECUTE FUNCTION annotation.scope_keeps_definitions();
CREATE TRIGGER concept_scheme_keeps_definitions
    AFTER UPDATE OF scope_resource_id ON vocab.concept_scheme
    FOR EACH ROW EXECUTE FUNCTION annotation.scope_keeps_definitions();
