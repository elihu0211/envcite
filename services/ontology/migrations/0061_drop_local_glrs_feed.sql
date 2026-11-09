-- 0061_drop_local_glrs_feed — 移除沒有來源的取得 feed 值 `local_glrs`
--
-- 0058 為「地方自治法規」取得器加了 `source.feed` 的 `local_glrs`。取得器假設的來源
-- （`glrs.moi.gov.tw/api/local/json`）實測回 404：該網站是內政部主管法規共用系統，只收內政部主管
-- 法規，沒有批次下載或 API；全國法規資料庫的批次下載只有中央法規；沒有官方單一的縣市自治法規
-- 彙整資料集，各縣市各有自己的系統與授權。取得器從未取得過任何資料，這個值沒有生產者，
-- 隨取得器一併移除。
--
-- `corpus.id_scheme` 的 `local_glrs` 不在此列：它是法規自然鍵裡「地方方案」的契約預留位置，
-- 與取得無關（見 0060）；日後有地方法規的取得來源，由那個來源的遷移宣告它的方案。
--
-- PostgreSQL 的列舉型別不能刪值，所以 `source.feed` 以重建取代（同 0060 對 `corpus.id_scheme`）。
-- 約束以舊型別的字面值儲存，型別一換就無法重新解析，所以引用 feed 字面值的約束先拆、換型別後再
-- 加回（內容不變）：`source.item` 的 `item_check`、`dropped_after_two_missed_crawls`，
-- `guidance.cessation` 的 `cessation_dropped_feed_check` 與外鍵 `taken_down_on_drop`。
-- 索引隨欄位自動重建；`guidance.settle_crawl` 只用字串字面值，不依賴型別。

-- 既有資料若含將被移除的值，整個遷移失敗，不悄悄丟資料。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM source.snapshot WHERE feed = 'local_glrs')
       OR EXISTS (SELECT 1 FROM source.item WHERE feed = 'local_glrs') THEN
        RAISE EXCEPTION 'source holds rows of feed local_glrs, which this migration removes'
            USING ERRCODE = 'data_exception';
    END IF;
END
$$;

ALTER TABLE guidance.cessation
    DROP CONSTRAINT taken_down_on_drop,
    DROP CONSTRAINT cessation_dropped_feed_check;

ALTER TABLE source.item
    DROP CONSTRAINT item_check,
    DROP CONSTRAINT dropped_after_two_missed_crawls;

ALTER TYPE source.feed RENAME TO feed_retired;
CREATE TYPE source.feed AS ENUM (
    'moj_law', 'moj_order', 'gazette', 'moenv_guidance', 'moenv_law', 'ly_law'
);

ALTER TABLE source.snapshot
    ALTER COLUMN feed TYPE source.feed USING feed::text::source.feed;
ALTER TABLE source.item
    ALTER COLUMN feed TYPE source.feed USING feed::text::source.feed;
ALTER TABLE guidance.cessation
    ALTER COLUMN dropped_feed TYPE source.feed USING dropped_feed::text::source.feed;

DROP TYPE source.feed_retired;

ALTER TABLE source.item
    ADD CONSTRAINT item_check
        CHECK (feed = 'moenv_guidance' OR missed_crawls = 0),
    ADD CONSTRAINT dropped_after_two_missed_crawls
        CHECK (feed <> 'moenv_guidance' OR (dropped_on IS NOT NULL) = (missed_crawls = 2));

ALTER TABLE guidance.cessation
    ADD CONSTRAINT cessation_dropped_feed_check
        CHECK (dropped_feed = 'moenv_guidance'),
    ADD CONSTRAINT taken_down_on_drop FOREIGN KEY (dropped_feed, dropped_key, ceased_on)
        REFERENCES source.item (feed, key, dropped_on);
