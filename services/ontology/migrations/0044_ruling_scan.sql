-- 0044_ruling_scan — 憲法法庭裁判頁的掃描紀錄
--
-- 取得流程逐頁抓憲法法庭網站；多數裁判並未宣告失效，不會進 constitutional_ruling。沒有這張表，
-- 每次重跑都得把它們再抓一遍。紀錄頁面內容的雜湊與當時的抽取規則版本：內容沒變且規則版本相同
-- 才略過，規則改了（版本遞增）就重掃。

CREATE TABLE corpus.ruling_scan (
    source_url        text PRIMARY KEY CHECK (source_url ~ '^https://'),
    content_sha256    bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    extractor_version integer NOT NULL,
    scanned_at        timestamptz NOT NULL DEFAULT now()
);

-- 抽取出來的失效逐筆記規則版本與是否只涉條文一部分。
-- - extractor_version 為空是人載入的（envcite-ruling load），抽取流程絕不刪改；
--   有值的是抽取流程產生的，規則改版重跑時先刪後寫，所以放寬或收緊規則都收斂。
-- - partial：裁判只宣告條文的一部分（「關於…部分」「於此範圍內」）。失效仍記在整個條文單元上
--   （資料模型到項為止），讀者看 excerpt 核對範圍。
ALTER TABLE corpus.ruling_invalidation
    ADD COLUMN partial boolean NOT NULL DEFAULT false,
    ADD COLUMN extractor_version integer CHECK (extractor_version > 0);
