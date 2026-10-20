-- 0024_definition_verification — 用語定義的自動驗證與接受
--
-- 與規範陳述同一套道理（0014）：定義由抽取器（語言模型）寫入，狀態為 extracted；它是否成為推論
-- 用的事實，取決於驗證紀錄。每次驗證寫一列，接受政策只看紀錄：必要方法最近一次都通過、沒有任何
-- 方法最近一次失敗才升為 confirmed，有失敗則 rejected；每次狀態變更也記一列（方法 policy）。
--
-- 抽取者不得驗證自己的定義（資料庫強制）。

CREATE TABLE annotation.definition_verification (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    definition_id bigint NOT NULL REFERENCES annotation.term_definition ON DELETE CASCADE,
    method        text NOT NULL CHECK (method <> ''),
    verifier      text NOT NULL CHECK (verifier <> ''),
    verdict       annotation.verdict NOT NULL,
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX definition_verification_by_definition
    ON annotation.definition_verification (definition_id, method, id DESC);

CREATE FUNCTION annotation.record_definition_verification(
    p_definition bigint,
    p_method     text,
    p_verifier   text,
    p_verdict    annotation.verdict,
    p_evidence   jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_agent text;
    v_id    bigint;
BEGIN
    SELECT d.producer INTO v_agent
      FROM annotation.term_definition AS t
      JOIN annotation.derivation AS d ON d.id = t.derivation_id
     WHERE t.id = p_definition;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such definition: %', p_definition USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_verifier = v_agent THEN
        RAISE EXCEPTION 'the extractor cannot verify its own definition (%)', v_agent
            USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO annotation.definition_verification (definition_id, method, verifier, verdict, evidence)
    VALUES (p_definition, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- 接受政策：對每個 extracted 的定義，各方法取最近一次驗證。任一為 fail → rejected；
-- 否則 p_required 的每個方法最近一次都是 pass 或 not_applicable、且至少一個是 pass → confirmed；
-- 其餘維持 extracted。至少要求一個方法：空政策不構成接受。
CREATE FUNCTION annotation.settle_definitions(p_required text[])
RETURNS TABLE (confirmed bigint, rejected bigint)
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
            INSERT INTO annotation.definition_verification
                (definition_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'fail',
                    jsonb_build_object('action', 'rejected', 'required', to_jsonb(p_required)));
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.term_definition SET confirm_state = 'confirmed' WHERE id = s.id;
            INSERT INTO annotation.definition_verification
                (definition_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'pass',
                    jsonb_build_object('action', 'confirmed', 'required', to_jsonb(p_required)));
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT n_ok, n_no;
END
$$;
