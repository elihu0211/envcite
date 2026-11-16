-- 0071_judgment_fetch — 裁判書異動清單的取用佇列
--
-- JList 只交呼叫日往前第 7 天的 jid，隔夜不會再交。6 時前沒取完的留在這裡，
-- 下一夜先消化佇列，再要新的清單。本表沒有裁判本文。

CREATE TYPE judgment.fetch_status AS ENUM (
    'pending', 'stored', 'file_only', 'taken_down', 'unmapped'
);

COMMENT ON TYPE judgment.fetch_status IS
    'pending：尚未取完，下一夜再試；stored：全文已入庫；file_only：全文是檔案網址，不下載；'
    'taken_down：來源回應查無資料；unmapped：行政法院代碼不在審級表，不重試。';

CREATE TABLE judgment.fetch (
    jid         text PRIMARY KEY CHECK (jid <> ''),
    listed_on   date NOT NULL,
    status      judgment.fetch_status NOT NULL,
    last_error  text CHECK (last_error <> ''),
    updated_on  date NOT NULL DEFAULT CURRENT_DATE
);

COMMENT ON TABLE judgment.fetch IS
    '司法院異動清單的取用進度。再出現的 jid 改回 pending，因為內容可能已改。';

CREATE INDEX fetch_pending ON judgment.fetch (listed_on, jid) WHERE status = 'pending';

GRANT SELECT ON judgment.fetch TO canon_reader, canon_writer, canon_reviewer;
GRANT INSERT, UPDATE, DELETE ON judgment.fetch TO canon_writer;
GRANT INSERT ON judgment.fetch TO canon_reviewer;
