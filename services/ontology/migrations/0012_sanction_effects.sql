-- 0012_sanction_effects — 罰則的效果：非處罰的行政命令，與按次處罰
--
-- 設計見 envcite-docs 02-04 §6，緣由見 02-05 §5.7。
--
-- 水污染防治法第 40 條一句含罰鍰、通知限期改善、按次處罰、停工停業、廢止許可證。一個罰則
-- 陳述一個效果是對的：每個效果有自己的要件（情節重大、必要時、屆期仍未改善），拆成各自的
-- 陳述才能各自涵攝。缺的是兩個值：
--
-- - 改善命令等行政命令不是行政罰法第 1、2 條的處罰種類，加一個效果值 order，不把它塞進
--   警告性。
-- - 按次處罰是同一個效果可以反覆裁處，是這個效果的性質，加一個旗標 repeating。

ALTER TYPE annotation.legal_effect ADD VALUE 'order';

ALTER TABLE annotation.sanction ADD COLUMN repeating boolean NOT NULL DEFAULT false;
