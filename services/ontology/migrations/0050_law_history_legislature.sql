-- 0050_law_history_legislature — 法律的歷史版本取自立法院法律系統（02-02 §3）
--
-- 一份快照是一部法律在立法院法律系統的各版條文頁（內容部分）打包成的 JSON，項目鍵是全國法規資料庫的
-- 法規代碼。歷史版本寫成 text_authority = 'legislature' 的版本列：法律的文字以制定機關三讀通過、
-- 公布的為準，與主管機關系統的轉錄（competent_authority，只收命令）分開記。

ALTER TYPE source.feed ADD VALUE 'ly_law';
ALTER TYPE corpus.text_authority ADD VALUE 'legislature';
