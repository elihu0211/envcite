-- 0011_condition_judgement — 要件的「需判斷」標記
--
-- 設計見 env-law-docs 02-04 §6、01-02 §4.3，緣由見 02-05 §5.7。
--
-- 情節重大、必要時、環境特殊或需特予保護之水體這類不確定法律概念或裁量，不是由事實
-- 判定的要件。標記之後，推論程式不把它當成可從事實算出的值：呼叫端若提供值，視為其判斷，
-- 結論的依據軌跡標出倚賴了這個判斷；沒有提供則列為「需判斷」，不冒充缺事實。
--
-- 限值是數值，不是判斷，不得標記。

ALTER TABLE annotation.statement_condition
    ADD COLUMN judgement boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT statement_condition_limit_is_not_judgement CHECK (role <> 'limit' OR NOT judgement);
