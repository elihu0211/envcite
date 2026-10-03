-- 0015_column_names — 欄位名稱對齊法學與工程
--
-- 設計見 env-law-docs 02-04 §3。已套用的遷移不可改，所以這裡改名。
-- 制度名只用在欄位就是那個制度；分類寫出分的是什麼。約束與查詢的行為不變。
-- 函式本體存的是欄名文字，改名不會自動改寫，所以下面按新名稱重建仍在使用的函式。
-- 效力狀態的列舉值 in_force、施行事件 commencement、施行規則型別 commencement_rule 都不是欄位，不改。

ALTER TYPE annotation.review_state RENAME TO confirm_state;
ALTER TYPE annotation.deontic RENAME TO norm_modality;

ALTER TABLE corpus.legal_resource RENAME COLUMN kind TO norm_kind;
ALTER TABLE corpus.legal_expression RENAME COLUMN authority TO legal_value;
ALTER TABLE corpus.provision_version RENAME COLUMN in_force TO force_period;
ALTER TABLE corpus.provision_version RENAME COLUMN commencement TO commence_rule;
ALTER TABLE corpus.lifecycle_event RENAME COLUMN kind TO event_kind;
ALTER TABLE source.snapshot RENAME COLUMN attribution TO license_notice;
ALTER TABLE source.item RENAME COLUMN withdrawn_on TO dropped_on;
ALTER TABLE source.import_problem RENAME COLUMN level TO severity;
ALTER TABLE source.import_problem RENAME COLUMN code TO problem_code;
ALTER TABLE vocab.concept_label RENAME COLUMN kind TO label_kind;
ALTER TABLE annotation.derivation RENAME COLUMN agent TO producer;
ALTER TABLE annotation.derivation RENAME COLUMN agent_version TO producer_version;
ALTER TABLE annotation.statement RENAME COLUMN kind TO statement_kind;
ALTER TABLE annotation.statement RENAME COLUMN deontic TO norm_modality;
ALTER TABLE annotation.statement RENAME COLUMN review TO confirm_state;
ALTER TABLE annotation.statement_condition RENAME COLUMN role TO cond_role;
ALTER TABLE annotation.statement_condition RENAME COLUMN clause TO disjunct_no;
ALTER TABLE annotation.statement_condition RENAME COLUMN unit TO measure_unit;
ALTER TABLE annotation.statement_condition RENAME COLUMN judgement TO needs_assessment;
ALTER TABLE annotation.statement_override RENAME COLUMN winner_id TO prevailing_id;
ALTER TABLE annotation.statement_override RENAME COLUMN loser_id TO subordinate_id;
ALTER TABLE annotation.statement_override RENAME COLUMN basis TO priority_basis;
ALTER TABLE annotation.sanction RENAME COLUMN effect TO legal_effect;
ALTER TABLE annotation.sanction RENAME COLUMN repeating TO per_occasion;

ALTER INDEX annotation.statement_override_by_loser RENAME TO statement_override_by_subordinate;
ALTER TABLE annotation.statement_condition
    RENAME CONSTRAINT statement_condition_limit_is_not_judgement
    TO statement_condition_limit_is_not_assessment;

CREATE OR REPLACE FUNCTION corpus.version_iri(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (
        SELECT corpus.resource_iri(r.norm_kind, r.local_id) || '/' || e.version_date || '/' || e.language || '/' || p.path
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
          JOIN corpus.legal_resource AS r ON r.id = v.resource_id
         WHERE v.id = p_version
    );


CREATE OR REPLACE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (
    provision_id   bigint,
    path           text,
    provision_type corpus.provision_type,
    sort_key       integer[],
    version_id     bigint,
    status         corpus.provision_status
)
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
                      AND v.commence_rule = 'unconfirmed'
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

CREATE OR REPLACE FUNCTION retrieval.search_articles(
    p_terms     text,
    p_tokenizer text,
    p_as_of     date,
    p_language  text,
    p_limit     integer
)
RETURNS TABLE (
    version_id    bigint,
    resource_id   bigint,
    path          text,
    lexical_score real
)
LANGUAGE sql STABLE
AS $$
    WITH q AS (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    hit AS (
        SELECT p.resource_id, split_part(p.path, '/', 1) AS article_path,
               max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.lexical_index AS i
         CROSS JOIN q
          JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE i.tokenizer = p_tokenizer
           AND i.tsv @@ q.query
           AND v.superseded_at IS NULL
           AND v.force_period @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, split_part(p.path, '/', 1)
    )
    SELECT av.id, h.resource_id, h.article_path, h.score
      FROM hit AS h
      JOIN corpus.provision AS ap ON ap.resource_id = h.resource_id AND ap.path = h.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.force_period @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY h.score DESC, av.id
     LIMIT p_limit;
$$;

CREATE OR REPLACE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
RETURNS TABLE (threshold numeric, statement_id bigint)
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
                                     AND ls.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.cond_role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
$$;

CREATE OR REPLACE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (statement_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = s.derivation_id
     WHERE s.confirm_state <> 'rejected'
       AND (s.confirm_state = 'confirmed' OR d.method = 'rule')
$$;

CREATE OR REPLACE FUNCTION annotation.record_verification(
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
    SELECT d.producer INTO v_agent
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

CREATE OR REPLACE FUNCTION annotation.settle(p_required text[])
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
         WHERE st.confirm_state = 'extracted'
         GROUP BY st.id
    LOOP
        IF s.failed THEN
            UPDATE annotation.statement SET confirm_state = 'rejected' WHERE id = s.id;
            INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'fail',
                    jsonb_build_object('action', 'rejected', 'required', to_jsonb(p_required)));
            n_no := n_no + 1;
        ELSIF coalesce(s.satisfied, false) THEN
            UPDATE annotation.statement SET confirm_state = 'confirmed' WHERE id = s.id;
            INSERT INTO annotation.verification (statement_id, method, verifier, verdict, evidence)
            VALUES (s.id, 'policy', 'settle', 'pass',
                    jsonb_build_object('action', 'confirmed', 'required', to_jsonb(p_required)));
            n_ok := n_ok + 1;
        END IF;
    END LOOP;
    RETURN QUERY SELECT n_ok, n_no;
END
$$;
