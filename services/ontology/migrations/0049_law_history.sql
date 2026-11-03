-- 0049_law_history — 環境部主管法規查詢系統的歷史版本（02-02 §3）
--
-- 一份快照是一部法規的歷次版本頁（歷史清單、現行頁、各歷史頁的內容部分）打包成的 JSON，項目鍵是
-- 該系統的法規代碼（FL…、GL…）。不是全量單檔：取得流程不做「連續兩份未出現即下架」的推論。
-- 歷史版本寫成 text_authority = 'competent_authority' 的版本列，沿用既有的版本、條文單元版本與不重疊約束，
-- 綱要不必改。

ALTER TYPE source.feed ADD VALUE 'moenv_law';
