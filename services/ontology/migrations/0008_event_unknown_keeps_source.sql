-- 0008_event_unknown_keeps_source — 認不得的事件必須保留原文
--
-- 列舉值 `unknown` 由 0005 加入。新加的列舉值要先提交才能使用（既有資料庫上，型別不是
-- 同一個交易建立的），所以使用它的約束放在這個檔案，由遷移程式逐檔提交之後才套用。
ALTER TABLE corpus.lifecycle_event
    ADD CONSTRAINT lifecycle_event_unknown_keeps_source
        CHECK (kind <> 'unknown' OR source_text IS NOT NULL);
