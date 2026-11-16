-- 0070_judgment — 行政法院裁判：解釋依據，錨定到條文單元，查無資料時刪除
--
-- 裁判與函釋（0029）同屬解釋依據：不是構成要件基準，不改條文的效力區間，也不是推論的前提；
-- 只在結論旁附上「法院對所依條文的見解」。錨定同一套（單元，內容雜湊）：條文改字後，
-- 舊文字上的裁判不再自動附到新文字。
--
-- 與函釋有三處不同，所以另立 schema 而不併入 guidance：
-- - 裁判適用的是行為時或裁處時的文字，不是現行文字。錨點因此可以是已被取代的版本，
--   並記錄版本是裁判載明的（stated）還是以裁判日代替的（decided_on），後者必須是裁判日現行的版本。
-- - 來源自己的主鍵是 jid；同一 jid 再出現是以新內容取代同一筆，不是新的一件。
-- - 撤下是刪除：司法院開放資料的取用規範要求來源回應「查無資料」的裁判，取用者刪除先前取得的
--   內容。撤下不是狀態轉移，內容、錨定邊與一切衍生物都刪，只留 jid 與偵測日的墓碑。
--   此後凡新增依裁判而生的衍生資料（檢索索引、向量、摘要），其外鍵必須 ON DELETE CASCADE 到
--   judgment.decision，撤下才不需要另外逐表清理。
--
-- 由規則或人載入（derivation.method 不得為 llm），同函釋。

CREATE SCHEMA judgment;

CREATE TYPE judgment.instance AS ENUM ('supreme', 'high', 'district');
COMMENT ON TYPE judgment.instance IS
    '審級：最高行政法院、高等行政法院、地方行政法院。';

CREATE TYPE judgment.kind AS ENUM ('ordinary', 'grand_chamber');
COMMENT ON TYPE judgment.kind IS
    'ordinary：一般判決與裁定；grand_chamber：最高行政法院大法庭裁定（只拘束提案庭於該案）。';

CREATE TYPE judgment.version_basis AS ENUM ('stated', 'decided_on');
COMMENT ON TYPE judgment.version_basis IS
    'stated：裁判理由載明行為時、裁處時或修正前等版本，並對應到唯一版本；'
    'decided_on：理由未載明，以裁判日現行的版本錨定。';

CREATE TABLE judgment.decision (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- 來源主鍵。第一欄是法院別加類別碼，類別碼在末位：A 為行政類，其他類別不入庫
    jid            text NOT NULL UNIQUE
                   CHECK (jid ~ '^[A-Z]*A,'),
    decided_on     date NOT NULL,
    instance       judgment.instance NOT NULL,
    kind           judgment.kind NOT NULL DEFAULT 'ordinary',
    title          text NOT NULL CHECK (title <> ''),
    body           text NOT NULL CHECK (body <> ''),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    source_url     text CHECK (source_url <> ''),
    derivation_id  bigint NOT NULL REFERENCES annotation.derivation,
    -- 大法庭設於最高行政法院
    CHECK (kind <> 'grand_chamber' OR instance = 'supreme')
);

COMMENT ON COLUMN judgment.decision.title IS '裁判案由（來源的 JTITLE）。';
COMMENT ON COLUMN judgment.decision.body IS '裁判全文（來源的 JFULL）。撤下時刪除整列。';

CREATE INDEX decision_by_derivation ON judgment.decision (derivation_id);

-- 雜湊由資料庫算（同 guidance.document）：convert_to 不是 IMMUTABLE，不能用生成欄
CREATE FUNCTION judgment.decision_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.content_sha256 := sha256(convert_to(NEW.title || E'\n' || NEW.body, 'UTF8'));
    RETURN NEW;
END
$$;

CREATE TRIGGER decision_hash
    BEFORE INSERT OR UPDATE OF title, body ON judgment.decision
    FOR EACH ROW EXECUTE FUNCTION judgment.decision_hash();

CREATE FUNCTION judgment.loaded_by_people_or_rules() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT method FROM annotation.derivation WHERE id = NEW.derivation_id) = 'llm' THEN
        RAISE EXCEPTION 'judgments are loaded by people or rules, not extracted by a model'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER decision_loaded_by_people_or_rules
    BEFORE INSERT OR UPDATE OF derivation_id ON judgment.decision
    FOR EACH ROW EXECUTE FUNCTION judgment.loaded_by_people_or_rules();

CREATE TABLE judgment.anchor (
    decision_id    bigint NOT NULL REFERENCES judgment.decision ON DELETE CASCADE,
    provision_id   bigint NOT NULL REFERENCES corpus.provision ON DELETE CASCADE,
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    basis          judgment.version_basis NOT NULL,
    PRIMARY KEY (decision_id, provision_id, content_sha256)
);

CREATE INDEX anchor_by_provision ON judgment.anchor (provision_id, content_sha256);

CREATE TABLE judgment.takedown (
    jid         text PRIMARY KEY CHECK (jid <> ''),
    detected_on date NOT NULL
);

COMMENT ON TABLE judgment.takedown IS
    '撤下的墓碑：只有 jid 與偵測日，不留裁判的任何內容。同一 jid 再出現時載入會移除墓碑。';

-- 某些陳述在某一裁處日可附上的裁判：錨點正是陳述所讀的那段文字，裁判日不晚於裁處日。
-- 陳述在行為時是否現行，由呼叫端（推論）以陳述組決定，這裡不重做。
CREATE FUNCTION judgment.for_statements(p_statements bigint[], p_decided date)
RETURNS TABLE(decision_id bigint, statement_id bigint)
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

-- 權限：讀者只讀；載入者可寫入、取代（UPDATE）與撤下（DELETE），審核者只新增，同函釋。
-- 預設權限只及於這個 schema 之後新建的物件

GRANT USAGE ON SCHEMA judgment TO canon_reader, canon_writer, canon_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA judgment TO canon_reader, canon_writer, canon_reviewer;
GRANT INSERT ON ALL TABLES IN SCHEMA judgment TO canon_writer, canon_reviewer;
GRANT UPDATE, DELETE ON ALL TABLES IN SCHEMA judgment TO canon_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA judgment TO canon_writer, canon_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment
    GRANT SELECT ON TABLES TO canon_reader, canon_writer, canon_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment
    GRANT INSERT ON TABLES TO canon_writer, canon_reviewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment
    GRANT UPDATE, DELETE ON TABLES TO canon_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA judgment
    GRANT USAGE ON SEQUENCES TO canon_writer, canon_reviewer;
