-- 0004_drop_concept_canonical_unit — 移除沒有生產者的概念標準單位
--
-- 單位的真值在要件上（annotation.statement_condition.measure_unit）：門檻以該要件所載的單位表示，
-- 同一概念在不同要件可用不同單位，概念層沒有可導出的單一單位，另存一份只會成為第二個真值來源。
-- 詞彙文件的概念不帶單位，沒有管線寫入這一欄；依它換算案件數值的讀取一併移除。

ALTER TABLE vocab.concept DROP COLUMN canonical_unit;
