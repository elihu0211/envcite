-- 0026_claim — 陳述與用語定義的共同部分抽成超型別 annotation.claim
--
-- 兩者都是「某個產生者對原文的主張，經驗證後才成為推論用的事實」：來源推導、審核狀態、驗證紀錄、
-- 接受政策、狀態留痕與「抽取者不得驗證自己」原本各寫兩份（0014／0024、0025），改規則時必須兩邊同步。
-- 超型別只收這些共同部分；錨定（單元，內容雜湊，範圍）留在子型別，因為逐日解析本來就分開查。
--
-- - 子型別的 id 就是 claim 的 id（複合外鍵帶種類，一個主張只屬於一個子型別）。陳述保留原 id；
--   用語定義改發新 id，連帶更新成員與驗證紀錄。
-- - 驗證紀錄併成一張 annotation.verification（claim_id）；接受政策 settle 依種類執行，
--   因為兩者的必要方法不同。
-- - 子型別被刪（條文連帶刪除）時，主張與它的驗證紀錄跟著刪。
-- - 手動審核走 annotation.set_confirm_state，理由寫進留痕。

CREATE TYPE annotation.claim_kind AS ENUM ('statement', 'definition');

CREATE TABLE annotation.claim (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_kind    annotation.claim_kind NOT NULL,
    derivation_id bigint NOT NULL REFERENCES annotation.derivation,
    confirm_state annotation.confirm_state NOT NULL DEFAULT 'extracted',
    UNIQUE (id, claim_kind)
);

CREATE INDEX claim_by_derivation ON annotation.claim (derivation_id);
CREATE INDEX claim_extracted ON annotation.claim (claim_kind, id) WHERE confirm_state = 'extracted';

-- 依賴舊欄位的物件先拆，最後依新結構重建

DROP VIEW annotation.statement_health;
DROP VIEW annotation.gate_health;
DROP TRIGGER statement_state_changed ON annotation.statement;
DROP TRIGGER statement_loaded_settled ON annotation.statement;
DROP TRIGGER term_definition_state_changed ON annotation.term_definition;
DROP TRIGGER term_definition_loaded_settled ON annotation.term_definition;
DROP TRIGGER verification_not_by_extractor ON annotation.verification;
DROP TRIGGER definition_verification_not_by_extractor ON annotation.definition_verification;
DROP FUNCTION annotation.log_confirm_state();
DROP FUNCTION annotation.verifier_is_not_extractor();
DROP FUNCTION annotation.settle(text[]);
DROP FUNCTION annotation.settle_definitions(text[]);
DROP FUNCTION annotation.record_verification(bigint, text, text, annotation.verdict, jsonb);
DROP FUNCTION annotation.record_definition_verification(bigint, text, text, annotation.verdict, jsonb);

-- 陳述：保留原 id

INSERT INTO annotation.claim (id, claim_kind, derivation_id, confirm_state)
OVERRIDING SYSTEM VALUE
SELECT id, 'statement', derivation_id, confirm_state FROM annotation.statement;

SELECT setval(pg_get_serial_sequence('annotation.claim', 'id'),
              greatest((SELECT max(id) FROM annotation.claim), 0) + 1, false);

-- 用語定義：改發新 id

ALTER TABLE annotation.term_definition ADD COLUMN claim_id bigint;
UPDATE annotation.term_definition AS t
   SET claim_id = n.claim_id
  FROM (SELECT id, nextval(pg_get_serial_sequence('annotation.claim', 'id')) AS claim_id
          FROM annotation.term_definition ORDER BY id) AS n
 WHERE n.id = t.id;

INSERT INTO annotation.claim (id, claim_kind, derivation_id, confirm_state)
OVERRIDING SYSTEM VALUE
SELECT claim_id, 'definition', derivation_id, confirm_state FROM annotation.term_definition;

ALTER TABLE annotation.term_definition_member
    DROP CONSTRAINT term_definition_member_definition_id_fkey;
UPDATE annotation.term_definition_member AS m
   SET definition_id = t.claim_id
  FROM annotation.term_definition AS t
 WHERE t.id = m.definition_id;

-- 驗證紀錄併成一張

ALTER TABLE annotation.verification DROP CONSTRAINT verification_statement_id_fkey;
ALTER TABLE annotation.verification RENAME COLUMN statement_id TO claim_id;
ALTER TABLE annotation.verification
    RENAME CONSTRAINT verification_statement_id_not_null TO verification_claim_id_not_null;
ALTER INDEX annotation.verification_by_statement RENAME TO verification_by_claim;

INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence, verified_at)
SELECT t.claim_id, v.method, v.verifier, v.verdict, v.evidence, v.verified_at
  FROM annotation.definition_verification AS v
  JOIN annotation.term_definition AS t ON t.id = v.definition_id
 ORDER BY v.id;

DROP TABLE annotation.definition_verification;

ALTER TABLE annotation.verification
    ADD CONSTRAINT verification_claim_id_fkey
        FOREIGN KEY (claim_id) REFERENCES annotation.claim ON DELETE CASCADE;

-- 子型別的 id 改為指向主張

ALTER TABLE annotation.term_definition ALTER COLUMN id DROP IDENTITY;
-- 新舊 id 的範圍可能重疊，主鍵逐列檢查：先移到負數再翻回
UPDATE annotation.term_definition SET id = -claim_id;
UPDATE annotation.term_definition SET id = -id;
ALTER TABLE annotation.term_definition DROP COLUMN claim_id;
ALTER TABLE annotation.statement ALTER COLUMN id DROP IDENTITY;

ALTER TABLE annotation.statement
    DROP COLUMN derivation_id,
    DROP COLUMN confirm_state,
    ADD COLUMN claim_kind annotation.claim_kind
        GENERATED ALWAYS AS ('statement'::annotation.claim_kind) STORED,
    ADD CONSTRAINT statement_is_claim
        FOREIGN KEY (id, claim_kind) REFERENCES annotation.claim (id, claim_kind) ON DELETE CASCADE;

ALTER TABLE annotation.term_definition
    DROP COLUMN derivation_id,
    DROP COLUMN confirm_state,
    ADD COLUMN claim_kind annotation.claim_kind
        GENERATED ALWAYS AS ('definition'::annotation.claim_kind) STORED,
    ADD CONSTRAINT term_definition_is_claim
        FOREIGN KEY (id, claim_kind) REFERENCES annotation.claim (id, claim_kind) ON DELETE CASCADE;

ALTER TABLE annotation.term_definition_member
    ADD CONSTRAINT term_definition_member_definition_id_fkey
        FOREIGN KEY (definition_id) REFERENCES annotation.term_definition ON DELETE CASCADE;

CREATE FUNCTION annotation.drop_claim() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM annotation.claim WHERE id = OLD.id;
    RETURN NULL;
END
$$;

CREATE TRIGGER statement_drops_claim
    AFTER DELETE ON annotation.statement
    FOR EACH ROW EXECUTE FUNCTION annotation.drop_claim();

CREATE TRIGGER term_definition_drops_claim
    AFTER DELETE ON annotation.term_definition
    FOR EACH ROW EXECUTE FUNCTION annotation.drop_claim();

-- 逐日解析：狀態改讀主張

CREATE OR REPLACE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE(statement_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.claim AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
$$;

CREATE OR REPLACE FUNCTION annotation.definitions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE(definition_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT t.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.term_definition AS t ON t.provision_id = v.provision_id
                                          AND t.content_sha256 = v.content_sha256
      JOIN annotation.claim AS c ON c.id = t.id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE a.status = 'in_force'
       AND c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
$$;

CREATE OR REPLACE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
RETURNS TABLE(threshold numeric, statement_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT lim.threshold, ls.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
      JOIN annotation.claim AS lc ON lc.id = ls.id AND lc.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.condition_role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
$$;

-- 驗證、接受與留痕：一套

CREATE FUNCTION annotation.verifier_is_not_extractor() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_producer text;
BEGIN
    IF NEW.method = 'policy' THEN
        RETURN NEW;
    END IF;
    SELECT d.producer INTO v_producer
      FROM annotation.claim AS c
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.id = NEW.claim_id;
    IF NEW.verifier = v_producer THEN
        RAISE EXCEPTION 'the extractor cannot verify its own claim (%)', v_producer
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER verification_not_by_extractor
    BEFORE INSERT OR UPDATE OF claim_id, method, verifier ON annotation.verification
    FOR EACH ROW EXECUTE FUNCTION annotation.verifier_is_not_extractor();

CREATE FUNCTION annotation.record_verification(
    p_claim    bigint,
    p_method   text,
    p_verifier text,
    p_verdict  annotation.verdict,
    p_evidence jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE sql
AS $$
    INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
    VALUES (p_claim, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id
$$;

CREATE FUNCTION annotation.log_confirm_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO annotation.verification (claim_id, method, verifier, verdict, evidence)
    VALUES (
        NEW.id,
        'policy',
        coalesce(nullif(current_setting('envcite.actor', true), ''), 'manual:' || session_user),
        CASE NEW.confirm_state WHEN 'confirmed' THEN 'pass' WHEN 'rejected' THEN 'fail'
                               ELSE 'inconclusive' END::annotation.verdict,
        jsonb_build_object('action', NEW.confirm_state::text)
            || coalesce(nullif(current_setting('envcite.policy', true), '')::jsonb, '{}'::jsonb)
    );
    RETURN NULL;
END
$$;

CREATE TRIGGER claim_state_changed
    AFTER UPDATE OF confirm_state ON annotation.claim
    FOR EACH ROW WHEN (OLD.confirm_state IS DISTINCT FROM NEW.confirm_state)
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE TRIGGER claim_loaded_settled
    AFTER INSERT ON annotation.claim
    FOR EACH ROW WHEN (NEW.confirm_state <> 'extracted')
    EXECUTE FUNCTION annotation.log_confirm_state();

CREATE FUNCTION annotation.set_confirm_state(
    p_claim  bigint,
    p_state  annotation.confirm_state,
    p_reason text
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'a manual review needs a reason' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM set_config('envcite.policy', jsonb_build_object('reason', p_reason)::text, true);
    UPDATE annotation.claim SET confirm_state = p_state WHERE id = p_claim;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such claim: %', p_claim USING ERRCODE = 'foreign_key_violation';
    END IF;
    PERFORM set_config('envcite.policy', '', true);
END
$$;

CREATE FUNCTION annotation.settle(p_kind annotation.claim_kind, p_required text[])
RETURNS TABLE(confirmed bigint, rejected bigint)
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
            UPDATE annotation.claim SET confirm_state = 'rejected' WHERE id = s.id;
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.claim SET confirm_state = 'confirmed' WHERE id = s.id;
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    PERFORM set_config('envcite.actor', '', true);
    PERFORM set_config('envcite.policy', '', true);
    RETURN QUERY SELECT n_ok, n_no;
END
$$;

-- 監測：語意不變，仍只算規範陳述

CREATE VIEW annotation.statement_health AS
SELECT c.confirm_state::text AS state, count(*) AS statements
  FROM annotation.claim AS c
 WHERE c.claim_kind = 'statement'
 GROUP BY c.confirm_state;

COMMENT ON VIEW annotation.statement_health IS
    '各審核狀態的陳述數：extracted 待驗證、confirmed 可進推論、rejected 已駁回。';

CREATE VIEW annotation.gate_health AS
SELECT method, verdict::text AS verdict, count(*) AS statements
  FROM (SELECT DISTINCT ON (v.claim_id, v.method) v.claim_id, v.method, v.verdict
          FROM annotation.verification AS v
          JOIN annotation.claim AS c ON c.id = v.claim_id
         WHERE v.method <> 'policy' AND c.claim_kind = 'statement'
         ORDER BY v.claim_id, v.method, v.id DESC) AS latest
 GROUP BY method, verdict;

COMMENT ON VIEW annotation.gate_health IS
    '各驗證方法對規範陳述最近一次的結果分布：pass、fail、inconclusive、not_applicable。';
