-- 0060_jurisdiction_scheme_pairing — 管轄與識別碼方案的配對由綱要強制，移除沒有生產者的結構
--
-- 0058 把法規的自然鍵擴成（管轄，方案，在地識別碼），但管轄只受 `^TW-[A-Z0-9]+$` 約束：
-- `TW-CENTRAL` 配地方方案、`TW-XXX` 配中央方案都收得進來。本檔改成：
--   1. 管轄是封閉集合（列舉）：中央與 22 縣市。
--   2. 方案與管轄的配對由 CHECK 強制：中央方案只配中央管轄，地方方案只配地方管轄。
--      新增方案時 CHECK 的 ELSE 分支拒絕，逼著同一個遷移說明新方案屬於哪一級。
--   3. 沒有預設管轄：寫入者必須明說。
--   4. 移除 0058 加的、除預設值外沒有任何寫入者與讀取者的結構：識別碼方案 `ly_statute`、
--      `gazette_order`、`moenv_order`，以及 `approval_authority`、`approval_doc_no`、`ratified_at`、
--      `invalidated_by`、`invalidation_date`。違憲失效的真值是 `corpus.constitutional_ruling`
--      與 `corpus.invalidations_at`，`invalidated_by` 是第二個真值來源。
--
-- PostgreSQL 的列舉型別不能刪值，所以 `corpus.id_scheme` 以重建取代；依賴該欄的視圖
-- `guidance.law_name` 隨之重建。

CREATE TYPE corpus.jurisdiction AS ENUM (
    'TW-CENTRAL',
    -- 直轄市
    'TW-TPE', 'TW-NWT', 'TW-TAO', 'TW-TXG', 'TW-TNN', 'TW-KHH',
    -- 市
    'TW-KEE', 'TW-HSZ', 'TW-CYI',
    -- 縣
    'TW-HSQ', 'TW-MIA', 'TW-CHA', 'TW-NAN', 'TW-YUN', 'TW-CYQ', 'TW-PIF',
    'TW-ILA', 'TW-HUA', 'TW-TTT', 'TW-PEN', 'TW-KIN', 'TW-LIE'
);

COMMENT ON TYPE corpus.jurisdiction IS
    '法規的管轄：TW-CENTRAL 為中央，其餘為 ISO 3166-2:TW 的 22 個縣市。';

-- 既有資料若含將被移除的方案、欄位值或不在列舉內的管轄，整個遷移失敗，不悄悄丟資料。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM corpus.legal_resource
         WHERE id_scheme IN ('ly_statute', 'gazette_order', 'moenv_order')
            OR approval_authority IS NOT NULL OR approval_doc_no IS NOT NULL
            OR ratified_at IS NOT NULL OR invalidated_by IS NOT NULL
            OR invalidation_date IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'corpus.legal_resource holds rows that use the schemes or columns this migration removes'
            USING ERRCODE = 'data_exception';
    END IF;
    IF EXISTS (
        SELECT 1 FROM corpus.legal_resource
         WHERE jurisdiction NOT IN (SELECT unnest(enum_range(NULL::corpus.jurisdiction))::text)
    ) THEN
        RAISE EXCEPTION 'corpus.legal_resource holds a jurisdiction outside corpus.jurisdiction'
            USING ERRCODE = 'data_exception';
    END IF;
END
$$;

DROP VIEW guidance.law_name;

ALTER TABLE corpus.legal_resource
    DROP CONSTRAINT legal_resource_jurisdiction_shape,
    DROP CONSTRAINT legal_resource_pcode_shape,
    DROP COLUMN approval_authority,
    DROP COLUMN approval_doc_no,
    DROP COLUMN ratified_at,
    DROP COLUMN invalidated_by,
    DROP COLUMN invalidation_date,
    ALTER COLUMN jurisdiction DROP DEFAULT,
    ALTER COLUMN jurisdiction TYPE corpus.jurisdiction USING jurisdiction::corpus.jurisdiction;

ALTER TYPE corpus.id_scheme RENAME TO id_scheme_retired;
CREATE TYPE corpus.id_scheme AS ENUM (
    'moj_pcode',    -- 全國法規資料庫 pcode（中央）
    'local_glrs'    -- 地方法規（地方）
);
ALTER TABLE corpus.legal_resource
    ALTER COLUMN id_scheme TYPE corpus.id_scheme USING id_scheme::text::corpus.id_scheme;
DROP TYPE corpus.id_scheme_retired;

ALTER TABLE corpus.legal_resource
    ADD CONSTRAINT legal_resource_pcode_shape
        CHECK (id_scheme <> 'moj_pcode' OR local_id ~ '^[A-Z][0-9]{7}$'),
    ADD CONSTRAINT legal_resource_scheme_matches_jurisdiction
        CHECK (CASE id_scheme
                   WHEN 'moj_pcode' THEN jurisdiction = 'TW-CENTRAL'
                   WHEN 'local_glrs' THEN jurisdiction <> 'TW-CENTRAL'
                   ELSE false
               END);

COMMENT ON COLUMN corpus.legal_resource.jurisdiction IS
    '管轄；與識別碼方案配對（legal_resource_scheme_matches_jurisdiction）。沒有預設值。';

-- 配對之後，moj_pcode 只會出現在中央，所以（moj_pcode，在地識別碼）仍是唯一的；
-- 地方方案的在地識別碼只在管轄內唯一，自然鍵一律帶管轄。

-- 錨定時法規名稱 → pcode；定義自 0050，內容不變。
CREATE VIEW guidance.law_name AS
WITH named AS (
    SELECT guidance.name_key(e.title) AS name, r.local_id AS pcode, false AS alias
      FROM corpus.legal_expression AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     WHERE r.id_scheme = 'moj_pcode'
    UNION
    SELECT guidance.name_key(m[1]), r.local_id, true
      FROM corpus.lifecycle_event AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     CROSS JOIN LATERAL regexp_matches(
            guidance.name_key(e.source_text), '原名稱:([^;)]+);新名稱[:;]', 'g') AS m
     WHERE r.id_scheme = 'moj_pcode' AND e.source_text LIKE '%原名稱%'
)
SELECT name,
       CASE WHEN count(DISTINCT pcode) = 1 THEN min(pcode) END AS pcode,
       bool_and(alias) AS alias
  FROM named
 GROUP BY name;

COMMENT ON VIEW guidance.law_name IS
    '錨定的法規名稱：name 唯一；pcode 為空值表示這個名稱對到兩部以上法規（不錨定）；'
    'alias 為真表示只出現在沿革的更名紀錄（舊名）。';

GRANT SELECT ON guidance.law_name
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
