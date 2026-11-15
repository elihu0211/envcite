-- 0036_sanction_trigger — 效果的觸發：一個罰則以另一個效果為前提
--
-- 罰則以「處罰」連到被違反的陳述之外，還可能以另一個效果為前提：
--
-- - order_lapsed（命令屆期未履行）：按次處罰以限期改善屆期仍未完成為前提（水污染防治法第 40 條）。
--   前提必須是命令（legal_effect = order），以產生欄加複合外鍵強制，同 sanction 對陳述種類的作法。
-- - sanction_imposed（另一處分已作成）：環境講習以停工、停業或新臺幣五千元以上罰鍰的處分為前提
--   （環境教育法第 23 條），而且跨法。金額門檻是被觸發者自己的要件，不在這裡。
--
-- 一個罰則的數個前提彼此是「或」（任一處分作成即觸發）。「按次」旗標只表示可以反覆裁處，表示不了
-- 從哪一個期限起算，所以觸發另記一張關係表。觸發和例外、優先一樣是陳述之間的關係，兩方都封存
-- 之後才寫，不進指紋。

CREATE TYPE annotation.trigger_kind AS ENUM ('order_lapsed', 'sanction_imposed');

COMMENT ON TYPE annotation.trigger_kind IS
    'order_lapsed：命令屆期未履行；sanction_imposed：另一處分已作成。';

ALTER TABLE annotation.sanction
    ADD CONSTRAINT sanction_statement_effect UNIQUE (statement_id, legal_effect);

CREATE TABLE annotation.sanction_trigger (
    statement_id         bigint NOT NULL REFERENCES annotation.sanction (statement_id) ON DELETE CASCADE,
    trigger_statement_id bigint NOT NULL REFERENCES annotation.sanction (statement_id) ON DELETE CASCADE,
    trigger_kind         annotation.trigger_kind NOT NULL,
    -- 命令屆期未履行的前提必須是命令；另一處分已作成者不限效果（產生欄為空值時不檢查外鍵）
    trigger_effect       annotation.legal_effect
        GENERATED ALWAYS AS (CASE WHEN trigger_kind = 'order_lapsed' THEN 'order'::annotation.legal_effect END) STORED,
    PRIMARY KEY (statement_id, trigger_statement_id),
    CHECK (statement_id <> trigger_statement_id),
    CONSTRAINT sanction_trigger_lapsed_is_order FOREIGN KEY (trigger_statement_id, trigger_effect)
        REFERENCES annotation.sanction (statement_id, legal_effect) ON DELETE CASCADE
);

CREATE INDEX sanction_trigger_by_trigger ON annotation.sanction_trigger (trigger_statement_id);

COMMENT ON TABLE annotation.sanction_trigger IS
    '罰則 statement_id 以罰則 trigger_statement_id 的作成（或其命令屆期未履行）為前提。';
