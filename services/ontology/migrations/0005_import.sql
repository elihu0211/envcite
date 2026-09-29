-- 0005_import — 匯入所需的欄位與狀態
--
-- 設計見 env-law-docs 03-03。匯入管線寫入語料，並在取得紀錄上記下每部法規匯入了
-- 哪個內容、由哪一版解析器產生，以及解析時的問題清單。

-- 認不得的沿革寫法：事件照記、原文保留，不猜種類。
ALTER TYPE corpus.event_kind ADD VALUE 'unknown';

-- 只有年月或格式不合的沿革，日期為空值、原文保留；不以該月一日或任何日期頂替。
ALTER TABLE corpus.lifecycle_event
    ALTER COLUMN event_date DROP NOT NULL,
    ADD COLUMN source_text text CHECK (source_text <> ''),
    ADD CONSTRAINT lifecycle_event_dated_or_sourced
        CHECK (event_date IS NOT NULL OR source_text IS NOT NULL),
    ADD CONSTRAINT lifecycle_event_unknown_keeps_source
        CHECK (kind <> 'unknown' OR source_text IS NOT NULL);

-- 生效日如何得出（03-03 §6）。無法確定者不猜：效力區間為空值，由此欄區分「待確認」與
-- 「等命令定施行日」。
CREATE TYPE corpus.commencement_rule AS ENUM (
    'unchanged',    -- 最後一次公布未觸及：文字在此版本之前已存在，以版本日為界
    'promulgation', -- 明定自公布或發布日施行：起算至第三日（中央法規標準法第 13 條）
    'specified',    -- 特定施行日期（第 14 條）
    'by_order',     -- 以命令定施行日，命令尚未發布
    'unconfirmed'   -- 施行說明或條文無法機械地判定
);

ALTER TABLE corpus.provision_version ADD COLUMN commencement corpus.commencement_rule;
-- 0003 的樣本兩部法律皆自公布日施行。
UPDATE corpus.provision_version SET commencement = 'promulgation';
ALTER TABLE corpus.provision_version
    ALTER COLUMN commencement SET NOT NULL,
    ADD CONSTRAINT provision_version_interval_iff_known
        CHECK ((in_force IS NULL) = (commencement IN ('by_order', 'unconfirmed')));

-- 待確認的單元有文字、沒有效力區間：與「尚未施行」「已失效」分開回報。列舉值不能在
-- 加入它的同一個交易中使用，故重建型別與其唯一的使用者。
DROP FUNCTION corpus.provisions_at(bigint, text, date);
DROP TYPE corpus.provision_status;
CREATE TYPE corpus.provision_status AS ENUM (
    'in_force', 'text_unavailable', 'not_yet_in_force', 'no_longer_in_force',
    'commencement_unconfirmed'
);

-- 某日的法規，逐單元解析：每一單元取效力區間涵蓋該日的版本（03-02 §5）。
-- 該日尚未公布的單元不列出；不以其他版本的文字頂替無法取得的文字。
CREATE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date)
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
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.commencement = 'unconfirmed'
               ) THEN 'commencement_unconfirmed'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND (v.in_force IS NULL OR lower(v.in_force) > p_on)
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
              AND v.in_force @> p_on
      ) AS cur ON true
     WHERE p.resource_id = p_resource
       AND EXISTS (
           SELECT 1
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND e.version_date <= p_on
       )
     ORDER BY p.sort_key, p.path;
$$;


-- 匯入記錄：每部法規最後一次成功匯入的內容雜湊與解析器版本。內容雜湊不同於現行、或
-- 解析器版本不同者需要（重新）匯入。
ALTER TABLE source.item
    ADD COLUMN imported_sha256 bytea CHECK (octet_length(imported_sha256) = 32),
    ADD COLUMN parser_version  integer,
    ADD CONSTRAINT item_import_state CHECK ((imported_sha256 IS NULL) = (parser_version IS NULL));

-- 解析時發現的問題，每部法規每次匯入重寫一份。error 使該部法規不寫入，warning 待查。
CREATE TABLE source.import_problem (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id     bigint NOT NULL REFERENCES source.item ON DELETE CASCADE,
    snapshot_id bigint NOT NULL REFERENCES source.snapshot ON DELETE CASCADE,
    level       text NOT NULL CHECK (level IN ('error', 'warning')),
    code        text NOT NULL CHECK (code <> ''),
    detail      text NOT NULL DEFAULT ''
);

CREATE INDEX import_problem_by_item ON source.import_problem (item_id);
CREATE INDEX import_problem_by_code ON source.import_problem (level, code);
