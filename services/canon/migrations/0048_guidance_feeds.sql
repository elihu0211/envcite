-- 0048_guidance_feeds — 行政規則的兩個來源：行政院公報與環境部函釋
--
-- 兩者都不是全量單檔（02-05 §2）：公報每包只含幾個出刊日、函釋逐頁抓取，所以取得流程不做
-- 「連續兩份未出現即下架」的推論，`dropped_on` 對它們恆為空值。
-- - gazette：一份快照是一個 ZIP（一天或一個月的數個出刊日），項目鍵是 MetaId。
-- - moenv_guidance：一份快照是一頁函釋的欄位表（頁面其餘部分每次都不同，不存），項目鍵是正規化的發文字號。
-- 匯入問題（名稱對不上、發文日沒有現行條文、網頁內容與已載入的公文不同）沿用 source.import_problem。

ALTER TYPE source.feed ADD VALUE 'gazette';
ALTER TYPE source.feed ADD VALUE 'moenv_guidance';

-- 來源上的檔名（公報的 `115-10_01.zip`）：下載網址帶每次可能不同的雜湊，判斷某包是否已取得只能靠檔名。
ALTER TABLE source.snapshot ADD COLUMN origin_name text CHECK (origin_name <> '');

-- 不唯一：來源重發同名的包（內容不同）是另一份快照。
CREATE INDEX snapshot_by_origin ON source.snapshot (feed, origin_name)
    WHERE origin_name IS NOT NULL;
