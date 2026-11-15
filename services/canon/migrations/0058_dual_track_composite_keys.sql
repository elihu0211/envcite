-- 0058_dual_track_composite_keys — 中央與地方雙軌五領域：複合自然鍵、地方自治法規、效力審查欄位

-- 1. 擴充來源 feed 與識別碼方案
ALTER TYPE source.feed ADD VALUE IF NOT EXISTS 'local_glrs';

ALTER TYPE corpus.id_scheme ADD VALUE IF NOT EXISTS 'local_glrs';
ALTER TYPE corpus.id_scheme ADD VALUE IF NOT EXISTS 'ly_statute';
ALTER TYPE corpus.id_scheme ADD VALUE IF NOT EXISTS 'gazette_order';
ALTER TYPE corpus.id_scheme ADD VALUE IF NOT EXISTS 'moenv_order';

-- 2. 擴充 legal_resource 複合鍵管轄與雙軌審查/核定欄位
ALTER TABLE corpus.legal_resource
    ADD COLUMN IF NOT EXISTS jurisdiction text NOT NULL DEFAULT 'TW-CENTRAL',
    ADD COLUMN IF NOT EXISTS approval_authority text,
    ADD COLUMN IF NOT EXISTS approval_doc_no text,
    ADD COLUMN IF NOT EXISTS ratified_at date,
    ADD COLUMN IF NOT EXISTS invalidated_by text,
    ADD COLUMN IF NOT EXISTS invalidation_date date;

-- 管轄格式檢查：中央為 'TW-CENTRAL'，地方為 22 縣市代碼如 'TW-TPE', 'TW-NWT', 'TW-TXG', 'TW-TNN', 'TW-KHH' 等
ALTER TABLE corpus.legal_resource
    ADD CONSTRAINT legal_resource_jurisdiction_shape
    CHECK (jurisdiction ~ '^TW-[A-Z0-9]+$');

-- 3. 複合自然鍵約束：(jurisdiction, id_scheme, local_id)
ALTER TABLE corpus.legal_resource
    DROP CONSTRAINT IF EXISTS legal_resource_id_scheme_local_id_key;

ALTER TABLE corpus.legal_resource
    ADD CONSTRAINT legal_resource_jurisdiction_scheme_local_id_key
    UNIQUE (jurisdiction, id_scheme, local_id);
