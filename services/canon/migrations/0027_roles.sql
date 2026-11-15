-- 0027_roles — 依職責分三個群組角色；審核狀態與驗證紀錄只能經由函式寫入，留痕記登入身分
--
-- 0025、0026 的觸發器保證「狀態變更必留痕」，但留痕裡的來源是呼叫端自報的（envcite.actor），
-- 任何能寫表的連線都能冒充 settle、直接寫 policy 列。同一個資料庫角色之內，程式路徑彼此無從區分；
-- 能區分的只有角色。
--
-- - envcite_ontology_reader：讀。gRPC 伺服器。
-- - envcite_ontology_writer：讀寫語料、檢索與詞彙；標註只能新增（主張寫入後內容不可改、不可刪，
--   修正是駁回後重新載入），驗證紀錄只能經 record_verification、接受只能經 settle。
--   取得、匯入、投影與標註管線。
-- - envcite_ontology_reviewer：writer 之外，可手動審核（set_confirm_state）與載入已審核的主張。
--   權限各自直接授予，不靠角色之間的成員關係：成員關係要由能管理角色的身分授予，遷移的身分不一定有。
--
-- 角色是叢集層級的物件。遷移只在角色不存在時建立；正式環境的擁有者沒有建角色的權限，
-- 角色要先由部署庫的 CloudNativePG managed roles 建好（清單先行），否則本遷移失敗。
-- 登入身分由部署端各自加入對應群組。
--
-- 留痕：驗證表每列記 recorded_by（session_user，SECURITY DEFINER 之下也不變，呼叫端無從冒充）；
-- settle 與 set_confirm_state 自己寫 policy 列，觸發器只補記其他路徑（擁有者直接 UPDATE、
-- 載入已審核的主張），以同一交易內是否已有相符的 policy 列判斷，不靠呼叫端可設的參數。
-- 0027 之前的列 recorded_by 記為 unrecorded。

DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['envcite_ontology_reader', 'envcite_ontology_writer',
                             'envcite_ontology_reviewer'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END
$$;

GRANT USAGE ON SCHEMA corpus, retrieval, source, vocab, annotation
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA corpus, retrieval, source, vocab, annotation
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA corpus, retrieval, source, vocab
    TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT INSERT ON ALL TABLES IN SCHEMA annotation
    TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA corpus, retrieval, source, vocab, annotation
    TO envcite_ontology_writer, envcite_ontology_reviewer;
REVOKE INSERT ON annotation.verification FROM envcite_ontology_writer, envcite_ontology_reviewer;

ALTER DEFAULT PRIVILEGES IN SCHEMA corpus, retrieval, source, vocab, annotation
    GRANT SELECT ON TABLES
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA corpus, retrieval, source, vocab
    GRANT INSERT, UPDATE, DELETE ON TABLES TO envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA annotation
    GRANT INSERT ON TABLES TO envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA corpus, retrieval, source, vocab, annotation
    GRANT USAGE ON SEQUENCES TO envcite_ontology_writer, envcite_ontology_reviewer;

-- 條文被刪時標註連帶刪除（外鍵動作以表擁有者執行），主張也要跟著刪：寫入角色本身沒有刪除權
ALTER FUNCTION annotation.drop_claim() SECURITY DEFINER SET search_path = pg_catalog, pg_temp;

-- 留痕記登入身分

ALTER TABLE annotation.verification
    ADD COLUMN recorded_by text NOT NULL DEFAULT 'unrecorded',
    ADD COLUMN recorded_in xid8 NOT NULL DEFAULT '0';
ALTER TABLE annotation.verification
    ALTER COLUMN recorded_by SET DEFAULT session_user,
    ALTER COLUMN recorded_in SET DEFAULT pg_current_xact_id();

COMMENT ON COLUMN annotation.verification.recorded_by IS
    '寫入這列的登入身分（session_user）；verifier 是自報的方法或閘門名稱，這欄不是。';
COMMENT ON COLUMN annotation.verification.recorded_in IS
    '寫入這列的交易；觸發器據此判斷狀態變更是否已由 settle 或 set_confirm_state 記過。';

-- 已審核的主張只有 reviewer 能載入

CREATE FUNCTION annotation.loaded_settled_needs_reviewer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.confirm_state <> 'extracted'
       AND NOT pg_has_role(current_user, 'envcite_ontology_reviewer', 'MEMBER') THEN
        RAISE EXCEPTION 'only a reviewer can load a claim as %', NEW.confirm_state
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER claim_loaded_settled_by_reviewer
    BEFORE INSERT ON annotation.claim
    FOR EACH ROW EXECUTE FUNCTION annotation.loaded_settled_needs_reviewer();

-- 留痕：函式自己記，觸發器補記其他路徑

CREATE OR REPLACE FUNCTION annotation.log_confirm_state() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM annotation.verification AS v
                WHERE v.claim_id = NEW.id AND v.method = 'policy'
                  AND v.recorded_in = pg_current_xact_id()
                  AND v.evidence->>'action' = NEW.confirm_state::text) THEN
        RETURN NULL;
    END IF;
    INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
    VALUES (
        NEW.id,
        'policy',
        CASE TG_OP WHEN 'INSERT' THEN 'loaded' ELSE 'direct' END,
        CASE NEW.confirm_state WHEN 'confirmed' THEN 'pass' WHEN 'rejected' THEN 'fail'
                               ELSE 'inconclusive' END::annotation.verdict,
        jsonb_build_object('action', NEW.confirm_state::text)
    );
    RETURN NULL;
END
$$;

CREATE OR REPLACE FUNCTION annotation.record_verification(
    p_claim    bigint,
    p_method   text,
    p_verifier text,
    p_verdict  annotation.verdict,
    p_evidence jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_method = 'policy' THEN
        RAISE EXCEPTION 'policy records are written by settle and set_confirm_state'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
    VALUES (p_claim, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE OR REPLACE FUNCTION annotation.set_confirm_state(
    p_claim  bigint,
    p_state  annotation.confirm_state,
    p_reason text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'a manual review needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM annotation.claim WHERE id = p_claim) THEN
        RAISE EXCEPTION 'no such claim: %', p_claim USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
    VALUES (p_claim, 'policy', 'review',
            CASE p_state WHEN 'confirmed' THEN 'pass' WHEN 'rejected' THEN 'fail'
                         ELSE 'inconclusive' END::annotation.verdict,
            jsonb_build_object('action', p_state::text, 'reason', p_reason));
    UPDATE annotation.claim SET confirm_state = p_state WHERE id = p_claim;
END
$$;

CREATE OR REPLACE FUNCTION annotation.settle(p_kind annotation.claim_kind, p_required text[])
RETURNS TABLE(confirmed bigint, rejected bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
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
            SELECT DISTINCT ON (v.claim_id, v.method) v.claim_id, v.method, v.verdict
              FROM annotation.verification AS v
              JOIN annotation.claim AS c ON c.id = v.claim_id
             WHERE v.method <> 'policy' AND c.claim_kind = p_kind AND c.confirm_state = 'extracted'
             ORDER BY v.claim_id, v.method, v.id DESC
        )
        SELECT c.id,
               coalesce(bool_or(l.verdict = 'fail'), false) AS failed,
               (SELECT bool_and(EXISTS (SELECT 1 FROM latest AS x
                                         WHERE x.claim_id = c.id AND x.method = m
                                           AND x.verdict IN ('pass', 'not_applicable')))
                  FROM unnest(p_required) AS m)
               AND (SELECT bool_or(EXISTS (SELECT 1 FROM latest AS x
                                            WHERE x.claim_id = c.id AND x.method = m
                                              AND x.verdict = 'pass'))
                      FROM unnest(p_required) AS m) AS satisfied
          FROM annotation.claim AS c
          LEFT JOIN latest AS l ON l.claim_id = c.id
         WHERE c.claim_kind = p_kind AND c.confirm_state = 'extracted'
         GROUP BY c.id
    LOOP
        IF s.failed THEN
            INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'fail',
                    jsonb_build_object('action', 'rejected', 'required', to_jsonb(p_required)));
            UPDATE annotation.claim SET confirm_state = 'rejected' WHERE id = s.id;
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'pass',
                    jsonb_build_object('action', 'confirmed', 'required', to_jsonb(p_required)));
            UPDATE annotation.claim SET confirm_state = 'confirmed' WHERE id = s.id;
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT n_ok, n_no;
END
$$;

REVOKE EXECUTE ON FUNCTION annotation.record_verification(bigint, text, text, annotation.verdict, jsonb),
                           annotation.settle(annotation.claim_kind, text[]),
                           annotation.set_confirm_state(bigint, annotation.confirm_state, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION annotation.record_verification(bigint, text, text, annotation.verdict, jsonb),
                          annotation.settle(annotation.claim_kind, text[])
    TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT EXECUTE ON FUNCTION annotation.set_confirm_state(bigint, annotation.confirm_state, text)
    TO envcite_ontology_reviewer;
