-- 0001_eli_core — ELI 核心三層的關聯式綱要。
--
-- 對應關係（見 env-law-docs 的 01-01-ELI模型與驗證分層）：
--   eli:LegalResource            → legal_resource    抽象規範本身
--   eli:LegalExpression          → legal_expression  某語言某版本的文字
--   eli:LegalResourceSubdivision → subdivision       條、項、款
--
-- 標題掛在 legal_expression 而非 legal_resource：eli:title 的定義域是表達層。
-- 今天只有中文，這個區分完全沒有症狀——正因如此才在今天守住。

CREATE TABLE legal_resource (
    -- 在地識別碼（全國法規資料庫編號的 slug 形式）。小寫英數與連字號，不以連字號起訖。
    id_local      text PRIMARY KEY
                  CHECK (id_local ~ '^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$'),
    iri           text NOT NULL UNIQUE,
    -- eli:date_document。未知時為 NULL，不以空字串代替——「不知道」與「沒有」是兩件事。
    date_document date
);

CREATE TABLE legal_expression (
    iri         text PRIMARY KEY,
    resource_id text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    title       text NOT NULL CHECK (title <> ''),
    -- BCP 47 語言標籤，正規化為小寫（RDF 的語言標籤比對本就不分大小寫）。
    language    text NOT NULL CHECK (language = lower(language) AND language <> ''),
    -- 一部法規在同一語言下只有一個現行表達。
    UNIQUE (resource_id, language)
);

CREATE TABLE subdivision (
    iri         text PRIMARY KEY,
    resource_id text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    -- eli:number 的書面形式，例如 '1'、'5-1'（第五條之一）。
    number      text NOT NULL CHECK (number ~ '^[0-9]+(-[0-9]+)*$'),
    -- 閱讀順序鍵：'5-1' 要排在 '5' 之後、'6' 之前。字串排序會把 '10' 排在 '2' 前面，
    -- 故以整數陣列排序。由 number 衍生，不可手寫——衍生欄位不會與來源分岔。
    sort_key    integer[] GENERATED ALWAYS AS (string_to_array(number, '-')::integer[]) STORED,
    -- 條號在一部法規內唯一。
    UNIQUE (resource_id, number)
);

-- 依法規取條文並依閱讀順序排列，是本服務唯一的熱路徑查詢。
CREATE INDEX subdivision_reading_order ON subdivision (resource_id, sort_key);
