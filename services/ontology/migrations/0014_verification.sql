-- 0014_verification — 標註的自動驗證與接受
--
-- 設計見 env-law-docs 02-04 §6、01-09 §6、02-05 §5.9。
--
-- 系統不依賴人逐條審核標註。陳述由抽取器（語言模型）寫入，狀態為 extracted；它是否成為推論用的
-- 事實，取決於一組自動閘門的驗證結果：每個閘門是一個方法（金額與原文一致、引用與原文一致、可
-- 執行、往返驗證、獨立測試、跨家族評審…），每次驗證寫一列，連同證據。接受政策（settle）只看
-- 紀錄：必要的方法最近一次都通過、且沒有任何方法最近一次失敗，才升為 confirmed；有失敗則 rejected。
-- 每次狀態變更本身也記一列（方法 policy），所以 confirmed 與 rejected 都能追溯到依據。
--
-- 獨立性：抽取者不得驗證自己的陳述（驗證者與推導活動的 agent 相同則拒絕）。同一個模型家族換個
-- 名字仍擋不住，那要靠閘門組成（不同家族、確定性工具）；一致不等於正確，所以任何一個方法都不單獨
-- 構成接受，政策要求多個互相獨立的方法。

-- not_applicable：這個方法對這個陳述沒有可檢查之處（例如沒有罰則的陳述沒有金額可核）。它不是通過，
-- 也不是沒跑：政策要求「必要方法都不是 fail、inconclusive 或缺漏，且至少一個是 pass」。
CREATE TYPE annotation.verdict AS ENUM ('pass', 'fail', 'inconclusive', 'not_applicable');

CREATE TABLE annotation.verification (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    statement_id bigint NOT NULL REFERENCES annotation.statement ON DELETE CASCADE,
    method       text NOT NULL CHECK (method <> ''),
    verifier     text NOT NULL CHECK (verifier <> ''),
    verdict      annotation.verdict NOT NULL,
    evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- 「最近一次」以編號為準（時鐘可能相同），所以索引依編號遞減。
CREATE INDEX verification_by_statement ON annotation.verification (statement_id, method, id DESC);

-- 錨定解析不看審核狀態：某日有效的單元上，錨在它現行文字雜湊的所有陳述。可執行性檢查要在
-- 陳述被接受之前就評估它，所以解析與「接受與否」分開。
CREATE FUNCTION annotation.anchored_statements_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (statement_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT s.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS s ON s.provision_id = v.provision_id
                                    AND s.content_sha256 = v.content_sha256
     WHERE a.status = 'in_force'
$$;

-- 推論用的陳述 = 錨定的、未駁回的，且已確認或由規則推導。行為不變，改為建在上面。
CREATE OR REPLACE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (statement_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = s.derivation_id
     WHERE s.review <> 'rejected'
       AND (s.review = 'confirmed' OR d.method = 'rule')
$$;

CREATE FUNCTION annotation.record_verification(
    p_statement bigint,
    p_method    text,
    p_verifier  text,
    p_verdict   annotation.verdict,
    p_evidence  jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_agent text;
    v_id    bigint;
BEGIN
    SELECT d.agent INTO v_agent
      FROM annotation.statement AS s
      JOIN annotation.derivation AS d ON d.id = s.derivation_id
     WHERE s.id = p_statement;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such statement: %', p_statement USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF p_verifier = v_agent THEN
        RAISE EXCEPTION 'the extractor cannot verify its own statement (%)', v_agent
            USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
    VALUES (p_statement, p_method, p_verifier, p_verdict, p_evidence)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- 接受政策：對每個 extracted 的陳述，各方法取最近一次驗證。任一為 fail → rejected；
-- 否則 p_required 的每個方法最近一次都是 pass 或 not_applicable、且至少一個是 pass → confirmed；
-- 其餘（缺方法、inconclusive、全部 not_applicable）維持 extracted。回傳本次升為 confirmed 與
-- rejected 的數目。至少要求一個方法：空政策不構成接受。
CREATE FUNCTION annotation.settle(p_required text[])
RETURNS TABLE (confirmed bigint, rejected bigint)
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
         WHERE st.review = 'extracted'
         GROUP BY st.id
    LOOP
        IF s.failed THEN
            UPDATE annotation.statement SET review = 'rejected' WHERE id = s.id;
            INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'fail',
                    jsonb_build_object('action', 'rejected', 'required', to_jsonb(p_required)));
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.statement SET review = 'confirmed' WHERE id = s.id;
            INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'pass',
                    jsonb_build_object('action', 'confirmed', 'required', to_jsonb(p_required)));
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT n_ok, n_no;
END
$$;
