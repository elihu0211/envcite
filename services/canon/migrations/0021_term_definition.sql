-- 0021_term_definition — 立法定義：用語的效力範圍、法源與時間
--
-- 台灣法規慣用「本法用語，定義如下」，定義只及於本法（準用時才擴及他法），隨條文修正而變，
-- 受條文的效力期間約束。「甲屬於乙」的法律依據是這種定義條文或授權命令，不是詞彙的上位概念：
-- `vocab.concept.broader_id` 只供導覽，不進推論。
--
-- 兩個改動：
-- - 詞彙方案可以限定在一部法規（`scope_resource_id`）：立法定義的用語各歸其法；空值為跨法共用的
--   方案（限值概念要共用，授權命令與被授權的法律才能解析到同一個概念，見 `threshold_at`）。
-- - 用語定義 `annotation.term_definition`：錨定（單元，內容雜湊），與規範陳述同一個錨點與同一組
--   確認狀態；定義涵蓋的成員概念列在 `term_definition_member`。

ALTER TABLE vocab.concept_scheme
    ADD COLUMN scope_resource_id bigint REFERENCES corpus.legal_resource ON DELETE CASCADE;

-- 刪除一部法規時，限定在它的方案連同概念一起刪；仍被別部法規的陳述引用的概念照舊擋下刪除。
ALTER TABLE vocab.concept DROP CONSTRAINT concept_scheme_id_fkey;
ALTER TABLE vocab.concept
    ADD CONSTRAINT concept_scheme_id_fkey FOREIGN KEY (scheme_id)
        REFERENCES vocab.concept_scheme ON DELETE CASCADE;

CREATE TABLE annotation.term_definition (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provision_id    bigint NOT NULL REFERENCES corpus.provision ON DELETE CASCADE,
    content_sha256  bytea NOT NULL,
    span            int4range,
    term_id         bigint NOT NULL REFERENCES vocab.concept,
    definition_text text NOT NULL CHECK (definition_text <> ''),
    derivation_id   bigint NOT NULL REFERENCES annotation.derivation,
    confirm_state   annotation.confirm_state NOT NULL DEFAULT 'extracted',
    CHECK (octet_length(content_sha256) = 32)
);

CREATE TABLE annotation.term_definition_member (
    definition_id bigint NOT NULL REFERENCES annotation.term_definition ON DELETE CASCADE,
    member_id     bigint NOT NULL REFERENCES vocab.concept,
    PRIMARY KEY (definition_id, member_id)
);

CREATE INDEX term_definition_by_anchor ON annotation.term_definition (provision_id, content_sha256);
CREATE INDEX term_definition_by_term ON annotation.term_definition (term_id);
CREATE INDEX term_definition_member_by_member ON annotation.term_definition_member (member_id);

-- 某日有效單元上的用語定義：取該日現行列的雜湊，與定義錨定的雜湊相同者；只取已確認的，
-- 或由規則推導的，被拒絕的不取。
CREATE FUNCTION annotation.definitions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (definition_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT t.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.term_definition AS t ON t.provision_id = v.provision_id
                                          AND t.content_sha256 = v.content_sha256
      JOIN annotation.derivation AS d ON d.id = t.derivation_id
     WHERE a.status = 'in_force'
       AND t.confirm_state <> 'rejected'
       AND (t.confirm_state = 'confirmed' OR d.method = 'rule')
$$;
