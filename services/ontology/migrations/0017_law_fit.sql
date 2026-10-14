-- 0017_law_fit — 效果裁量，與幾個欄位的法學定性註解
--
-- 設計見 envcite-docs 02-04 §6、02-05 §5.7 與 01-02 §4.7。
--
-- 不確定法律概念（構成要件，涵攝，原則上全面受司法審查）與裁量（法律效果，「得」與否、
-- 額度的選擇）是兩個層次。needs_assessment 只屬前者；後者原本沒有位置，記在這裡：
-- 罰則條文寫「得」者為效果裁量，寫「應」者不是。

ALTER TABLE annotation.sanction ADD COLUMN discretionary boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN annotation.sanction.discretionary IS
    '效果裁量：條文寫「得」處罰或命令者為真，寫「應」者為假。與要件的 needs_assessment 是兩個層次。';
COMMENT ON COLUMN annotation.statement_condition.needs_assessment IS
    '構成要件的不確定法律概念（情節重大、必要時）：系統不從事實判定它。效果面的裁量見 sanction.discretionary。';
COMMENT ON COLUMN annotation.sanction.legal_effect IS
    'fine、confiscation 為行政罰法第 1 條；restriction、deprivation、reputation、warning 為同法第 2 條四類其他種類行政罰；'
    'order（改善命令等）是預防性（管制性）不利處分，不是行政罰，行政罰法的總則不適用。';
COMMENT ON TYPE annotation.override_basis IS
    'hierarchy：中央法規標準法第 11 條；lex_specialis：同法第 16 條；lex_posterior：學理（同法第 21 條第 4 款是廢止，不是優先），'
    '只在有明文標註時採用；exception：但書。';
