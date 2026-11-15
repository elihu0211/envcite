-- 0055_penalty_link — 罰則只連到已確認的義務；未連上的罰則與「被罰卻沒有義務」的條文是可查的狀態
--
-- 連結（annotation.sanction.punished_statement_id）隨罰則封存、不能改連，所以連結發生在義務有定論之後，
-- 由綱要強制：罰則列寫入時，所處罰的陳述必須已確認。抽取時還連不上的罰則存成待連結罰則
-- （annotation.held_penalty），等被引條文的義務確認再寫成陳述；未連上的原因由狀態檢視推得，不另存。
--
-- 處罰條文以「違反第 N 條」罰的條文由確定性的條號解析寫成依賴圖（annotation.violation，envcite-sync 全庫寫入）；
-- 被引卻沒有任何未駁回義務的條文是 annotation.duty_gap，待重新抽取。

-- duty：所罰義務所在的條、項、款；delegated_rule：「違反依第 N 條所定辦法」的授權條文（義務在授權命令裡）。
CREATE TYPE annotation.violation_kind AS ENUM ('duty', 'delegated_rule');

-- 處罰條文（某個文字版本）所罰的條文。語料沒有的引用 cited_provision_id 為 NULL。
CREATE TABLE annotation.violation (
    provision_id       bigint  NOT NULL REFERENCES corpus.provision (id) ON DELETE CASCADE,
    content_sha256     bytea   NOT NULL CHECK (octet_length(content_sha256) = 32),
    cited_path         text    NOT NULL CHECK (cited_path <> ''),
    cited_provision_id bigint  REFERENCES corpus.provision (id) ON DELETE CASCADE,
    violation_kind     annotation.violation_kind NOT NULL,
    rule_version       integer NOT NULL CHECK (rule_version > 0),
    PRIMARY KEY (provision_id, content_sha256, cited_path, violation_kind, rule_version)
);
CREATE INDEX violation_by_cited ON annotation.violation (cited_provision_id);

-- 每個處罰條文版本只看最新規則版本解析出的引用。
CREATE VIEW annotation.current_violation AS
SELECT vi.*
  FROM annotation.violation AS vi
 WHERE vi.rule_version = (SELECT max(x.rule_version) FROM annotation.violation AS x
                           WHERE x.provision_id = vi.provision_id
                             AND x.content_sha256 = vi.content_sha256);

-- 抽取到、還沒連上的罰則：一份是一個單元裡處罰同一對象的罰則（含彼此的觸發），document 是 annotate.load
-- 的文件。同單元受罰的陳述已知時記在 punished_statement_id，否則經 annotation.violation 找被引條文的義務。
CREATE TABLE annotation.held_penalty (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provision_id          bigint NOT NULL REFERENCES corpus.provision (id) ON DELETE CASCADE,
    content_sha256        bytea  NOT NULL CHECK (octet_length(content_sha256) = 32),
    producer              text   NOT NULL CHECK (producer <> ''),
    producer_version      text   NOT NULL CHECK (producer_version <> ''),
    punished_statement_id bigint REFERENCES annotation.statement (id) ON DELETE CASCADE,
    document              jsonb  NOT NULL CHECK (jsonb_typeof(document -> 'statements') = 'array'),
    -- 文件與處罰對象的雜湊：同一份抽取重載不重複存
    fingerprint           bytea  NOT NULL CHECK (octet_length(fingerprint) = 32),
    held_at               timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (provision_id, content_sha256, fingerprint)
);
CREATE INDEX held_penalty_by_anchor ON annotation.held_penalty (provision_id, content_sha256);

-- 待連結罰則寫成的罰則陳述（一個被處罰的義務一份）。
CREATE TABLE annotation.held_penalty_link (
    held_penalty_id bigint NOT NULL REFERENCES annotation.held_penalty (id) ON DELETE CASCADE,
    statement_id    bigint NOT NULL REFERENCES annotation.sanction (statement_id) ON DELETE CASCADE,
    PRIMARY KEY (held_penalty_id, statement_id)
);

-- 待連結罰則的候選：同單元已知的受罰陳述，或被引條文（含其下的項款、或引款而錨在項的列舉句）裡、
-- 現行文字上、未駁回的義務與禁止陳述。
CREATE FUNCTION annotation.penalty_candidates(p_held bigint)
RETURNS TABLE (statement_id bigint, path text, confirmed boolean)
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

-- linked：已寫成陳述；linkable：有已確認的候選；awaiting_duty：候選都還沒有定論；
-- no_duty：被引條文沒有未駁回的義務（見 duty_gap）；no_citation：既不知受罰陳述、也解析不出引用。
CREATE VIEW annotation.held_penalty_state AS
SELECT h.id, h.provision_id, h.content_sha256, h.producer, h.producer_version,
       CASE
           WHEN EXISTS (SELECT 1 FROM annotation.held_penalty_link AS l WHERE l.held_penalty_id = h.id)
               THEN 'linked'
           WHEN EXISTS (SELECT 1 FROM annotation.penalty_candidates(h.id) AS c WHERE c.confirmed)
               THEN 'linkable'
           WHEN EXISTS (SELECT 1 FROM annotation.penalty_candidates(h.id))
               THEN 'awaiting_duty'
           WHEN h.punished_statement_id IS NULL AND NOT EXISTS (
                   SELECT 1 FROM annotation.current_violation AS vi
                    WHERE vi.provision_id = h.provision_id AND vi.content_sha256 = h.content_sha256
                      AND vi.violation_kind = 'duty' AND vi.cited_provision_id IS NOT NULL)
               THEN 'no_citation'
           ELSE 'no_duty'
       END AS state
  FROM annotation.held_penalty AS h;

-- 被處罰條文以「違反」引用、現行文字上卻沒有任何未駁回義務或禁止陳述的條文：該重新抽取。
CREATE VIEW annotation.duty_gap AS
SELECT DISTINCT c.id AS provision_id, c.path, vi.provision_id AS penalty_provision_id
  FROM annotation.current_violation AS vi
  JOIN corpus.provision_version AS pv
    ON pv.provision_id = vi.provision_id AND pv.content_sha256 = vi.content_sha256
   AND pv.superseded_at IS NULL
  JOIN corpus.provision AS c ON c.id = vi.cited_provision_id
 WHERE vi.violation_kind = 'duty' AND NOT EXISTS (
       SELECT 1
         FROM corpus.provision AS p
         JOIN annotation.statement AS s ON s.provision_id = p.id
         JOIN corpus.provision_version AS v
           ON v.provision_id = s.provision_id AND v.content_sha256 = s.content_sha256
          AND v.superseded_at IS NULL
         JOIN annotation.assertion AS a ON a.id = s.id
        WHERE p.resource_id = c.resource_id
          AND (p.path = c.path OR p.path LIKE c.path || '/%' OR c.path LIKE p.path || '/%')
          AND s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')
          AND a.confirm_state <> 'rejected');

-- 罰則列寫入時，所處罰的陳述必須已確認。
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

CREATE TRIGGER sanction_punishes_confirmed
BEFORE INSERT OR UPDATE OF punished_statement_id ON annotation.sanction
FOR EACH ROW EXECUTE FUNCTION annotation.sanction_punishes_confirmed();

-- 接受：所處罰的陳述已不是 confirmed（之後被手動駁回或退回）的罰則不接受；被駁回則罰則駁回。
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
$function$;

GRANT SELECT ON annotation.violation, annotation.current_violation, annotation.held_penalty, annotation.held_penalty_link,
                annotation.held_penalty_state, annotation.duty_gap
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT INSERT ON annotation.violation, annotation.held_penalty, annotation.held_penalty_link
    TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT USAGE ON SEQUENCE annotation.held_penalty_id_seq TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT EXECUTE ON FUNCTION annotation.penalty_candidates(bigint)
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
