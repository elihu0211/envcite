-- 0052_temporal_invariants — 版本時間軸的不變式由綱要強制
--
-- 1. 版本不留縫：除了最新一版，每一版的文字時間都止於同一部法規、同一語文的另一版的版本日
--    （自參照的外鍵，延遲到交易結束檢查，一次改寫數版時中途可以不相接）。不重疊已由
--    legal_expression 的 WITHOUT OVERLAPS 強制。
-- 2. 查不到全文的版本也是一版：text_status = 'unavailable'，沒有條文單元版本，標題可以不知道。
-- 3. 施行規則與版本日的關係（條文單元版本帶著所屬版本的版本日：寫入時由觸發器自版本列帶入，
--    以複合外鍵保證與版本一致，寫入者不必知道這一欄）：
--    unchanged 自版本日、promulgation 自版本日起算第三日、specified 不早於版本日、retroactive 早於版本日；
--    by_order、unconfirmed 沒有效力區間（既有的 provision_version_force_follows_rule）。只約束現行列：
--    被取代的列是當時的紀錄，內容不改。

-- 2.
ALTER TABLE corpus.legal_expression ALTER COLUMN title DROP NOT NULL;
ALTER TABLE corpus.legal_expression
    ADD CONSTRAINT legal_expression_title_known
        CHECK (title IS NOT NULL OR text_status = 'unavailable');

-- 1.
ALTER TABLE corpus.legal_expression
    ADD COLUMN text_until date GENERATED ALWAYS AS (upper(text_period)) STORED;
ALTER TABLE corpus.legal_expression
    ADD CONSTRAINT legal_expression_text_continues
        FOREIGN KEY (resource_id, language, text_until)
        REFERENCES corpus.legal_expression (resource_id, language, version_date)
        DEFERRABLE INITIALLY DEFERRED;

-- 3.
ALTER TABLE corpus.provision_version ADD COLUMN expression_date date;
UPDATE corpus.provision_version AS v
   SET expression_date = e.version_date
  FROM corpus.legal_expression AS e
 WHERE e.id = v.expression_id;
UPDATE corpus.provision_version AS v
   SET commencement_rule = 'retroactive'
 WHERE v.superseded_at IS NULL
   AND v.commencement_rule = 'specified'
   AND lower(v.force_period) < v.expression_date;
ALTER TABLE corpus.provision_version ALTER COLUMN expression_date SET NOT NULL;

CREATE FUNCTION corpus.provision_version_expression_date() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    SELECT e.version_date INTO NEW.expression_date
      FROM corpus.legal_expression AS e
     WHERE e.id = NEW.expression_id;
    RETURN NEW;
END
$$;
CREATE TRIGGER provision_version_expression_date
    BEFORE INSERT OR UPDATE OF expression_id ON corpus.provision_version
    FOR EACH ROW EXECUTE FUNCTION corpus.provision_version_expression_date();
ALTER TABLE corpus.provision_version
    ADD CONSTRAINT provision_version_expression_date
        FOREIGN KEY (expression_id, resource_id, expression_date)
        REFERENCES corpus.legal_expression (id, resource_id, version_date) ON DELETE CASCADE,
    ADD CONSTRAINT provision_version_start_follows_rule
        CHECK (superseded_at IS NOT NULL OR CASE commencement_rule
            WHEN 'unchanged' THEN lower(force_period) = expression_date
            WHEN 'promulgation' THEN lower(force_period) = expression_date + 2
            WHEN 'specified' THEN lower(force_period) >= expression_date
            WHEN 'retroactive' THEN lower(force_period) < expression_date
            ELSE force_period IS NULL
        END);
