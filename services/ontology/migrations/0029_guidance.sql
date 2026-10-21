-- 0029_guidance — 行政規則另立一軸：函釋、裁量基準與作業性規定
--
-- 行政規則（行政程序法第 159 條）是上級機關對下級機關的一般、抽象規定，拘束機關內部，
-- 不直接對外發生法規範效力；法官依法審判不受其拘束，但得引用（釋字第 137、216 號）。
-- 所以它不進 corpus 的位階，也不是推論的前提：推論結論旁附上主管機關的見解，供呼叫端參考。
-- 0001 在 norm_kind 留了 administrative_rule，但行政規則多為一件公文，沒有條、項、款，
-- 裝不進 corpus.provision；那個值不再使用。
--
-- - document：一件行政規則（發文機關、字號、發文日、主旨與說明）。公文本身不可改；
--   內容以雜湊記錄，重新載入同一件時內容必須相同。
-- - interpretation：本件解釋哪一個條文單元的哪一段文字。錨定（單元，內容雜湊），與陳述同一套：
--   條文修正而文字改了，解釋就不再自動附上，要重新確認它是否仍適用於新文字。
-- - cessation：停止適用。後函變更前釋示時，前函自後函發文日起不再援用（釋字第 287 號）；
--   沒有後函的停止（逕行廢止）只記日期。
-- - 時態（釋字第 287 號）：解釋性規定闡明法規原意，原則上自所解釋的法規生效日起有效，所以
--   未確定的案件即使行為早於發文，裁處時已發布、尚未停止者即可附上；所解釋的文字須在行為時現行。
-- - 由人或規則載入（derivation.method 為 human 或 rule），不是模型的主張，沒有審核狀態；
--   改由模型抽取時另立主張子型別。

CREATE SCHEMA guidance;

CREATE TYPE guidance.rule_kind AS ENUM ('interpretation', 'discretion_standard', 'operational');

COMMENT ON TYPE guidance.rule_kind IS
    'interpretation、discretion_standard：行政程序法第 159 條第 2 項第 2 款（解釋性規定、裁量基準）；'
    'operational：同項第 1 款（組織性、作業性規定）。';

CREATE TABLE guidance.document (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer         text NOT NULL CHECK (issuer <> ''),
    document_no    text NOT NULL CHECK (document_no <> ''),
    issued_on      date NOT NULL,
    rule_kind      guidance.rule_kind NOT NULL,
    subject        text NOT NULL CHECK (subject <> ''),
    body           text NOT NULL CHECK (body <> ''),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    published_in   text CHECK (published_in <> ''),
    source_url     text CHECK (source_url <> ''),
    derivation_id  bigint NOT NULL REFERENCES annotation.derivation,
    UNIQUE (issuer, document_no),
    UNIQUE (id, issued_on)
);

COMMENT ON COLUMN guidance.document.published_in IS '公報出處（行政程序法第 160 條第 2 項）。';

CREATE INDEX document_by_derivation ON guidance.document (derivation_id);

-- 雜湊由資料庫算（同 corpus.provision_version）：convert_to 不是 IMMUTABLE，不能用生成欄
CREATE FUNCTION guidance.document_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(NEW.subject || E'\n' || NEW.body, 'UTF8'));
    RETURN NEW;
END
$$;

CREATE TRIGGER document_hash
    BEFORE INSERT OR UPDATE OF subject, body ON guidance.document
    FOR EACH ROW EXECUTE FUNCTION guidance.document_hash();

CREATE FUNCTION guidance.loaded_by_people_or_rules() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT method FROM annotation.derivation WHERE id = NEW.derivation_id) = 'llm' THEN
        RAISE EXCEPTION 'administrative rules are loaded by people or rules, not extracted by a model'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER document_loaded_by_people_or_rules
    BEFORE INSERT ON guidance.document
    FOR EACH ROW EXECUTE FUNCTION guidance.loaded_by_people_or_rules();

CREATE TABLE guidance.interpretation (
    document_id    bigint NOT NULL REFERENCES guidance.document ON DELETE CASCADE,
    provision_id   bigint NOT NULL REFERENCES corpus.provision ON DELETE CASCADE,
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    PRIMARY KEY (document_id, provision_id, content_sha256)
);

CREATE INDEX interpretation_by_anchor ON guidance.interpretation (provision_id, content_sha256);

CREATE TABLE guidance.cessation (
    document_id    bigint PRIMARY KEY REFERENCES guidance.document ON DELETE CASCADE,
    ceased_on      date NOT NULL,
    by_document_id bigint,
    CHECK (by_document_id <> document_id),
    -- 後函停止前函時，停止日就是後函的發文日
    FOREIGN KEY (by_document_id, ceased_on) REFERENCES guidance.document (id, issued_on)
);

CREATE INDEX cessation_by_successor ON guidance.cessation (by_document_id)
    WHERE by_document_id IS NOT NULL;

CREATE FUNCTION guidance.ceases_after_issue() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.ceased_on < (SELECT issued_on FROM guidance.document WHERE id = NEW.document_id) THEN
        RAISE EXCEPTION 'a rule cannot cease before it was issued' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER cessation_after_issue
    BEFORE INSERT OR UPDATE ON guidance.cessation
    FOR EACH ROW EXECUTE FUNCTION guidance.ceases_after_issue();

-- 某些陳述在某一裁處日可附上的行政規則：解釋的正是陳述所讀的那段文字，裁處時已發布、尚未停止。
-- 陳述在行為時是否現行，由呼叫端（推論）以陳述組決定，這裡不重做。
CREATE FUNCTION guidance.for_statements(p_statements bigint[], p_decided date)
RETURNS TABLE(document_id bigint, statement_id bigint)
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

-- 權限：公文不可改；寫入者只新增（0027 的預設權限只及於當時列出的 schema）

GRANT USAGE ON SCHEMA guidance
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA guidance
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT INSERT ON ALL TABLES IN SCHEMA guidance TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA guidance TO envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance
    GRANT SELECT ON TABLES
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance
    GRANT INSERT ON TABLES TO envcite_ontology_writer, envcite_ontology_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA guidance
    GRANT USAGE ON SEQUENCES TO envcite_ontology_writer, envcite_ontology_reviewer;
