-- 0004_source — 取得紀錄：原始快照與來源項目
--
-- 設計見 env-law-docs 02-04 §4 與 03-01 §9。原始檔以雜湊為名存於檔案系統，此處只記
-- 中繼資料。解析器版本與處理狀態隨解析管線加入。

CREATE SCHEMA source;

-- 來源的一個全量檔。全國法規資料庫的法律與命令各為一個。
CREATE TYPE source.feed AS ENUM ('moj_law', 'moj_order');

CREATE TABLE source.snapshot (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed        source.feed NOT NULL,
    -- 來源自己標示的資料日期，不是取得日期。
    data_date   date NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    sha256      bytea NOT NULL CHECK (octet_length(sha256) = 32),
    size        bigint NOT NULL CHECK (size > 0),
    -- 相對於快照根目錄的路徑。
    location    text NOT NULL CHECK (location <> ''),
    attribution text NOT NULL CHECK (attribution <> ''),
    -- 同一份檔案只處理一次：排程重跑或同日重複執行都是無害的。
    UNIQUE (feed, sha256)
);

-- 來源內的一個項目（全國法規資料庫為一部法規）。內容雜湊改變即有新版本；
-- 連續兩份全量快照未出現，即記下架日。
CREATE TABLE source.item (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed              source.feed NOT NULL,
    key               text NOT NULL CHECK (key <> ''),
    content_sha256    bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    first_snapshot_id bigint NOT NULL REFERENCES source.snapshot,
    last_snapshot_id  bigint NOT NULL REFERENCES source.snapshot,
    withdrawn_on      date,
    UNIQUE (feed, key)
);
