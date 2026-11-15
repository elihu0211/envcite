-- 0022_drop_broader — 受控詞彙不再借用 SKOS 的上位概念
--
-- 0010 把詞彙寫成「SKOS 的子集」，但本系統不以 RDF 發佈、不與外部詞表互通，SKOS 帶不來互通的
-- 好處；借來的 `broader_id` 只寫不讀，留著只會被誤當成可推論的上下位。台灣法上「甲屬於乙」的
-- 依據是立法定義或授權命令（`annotation.term_definition`，0021），不是詞彙的階層。
--
-- 受控詞彙現在就是：方案、概念的代碼與名稱、別名。`UNIQUE (id, scheme_id)` 只為上位概念的
-- 複合外鍵而設，一併移除。

ALTER TABLE vocab.concept DROP COLUMN broader_id;
ALTER TABLE vocab.concept DROP CONSTRAINT concept_id_scheme_id_key;
