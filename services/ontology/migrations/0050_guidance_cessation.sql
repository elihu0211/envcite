-- 0050_guidance_cessation — 行政規則停止適用的三個來源，與錨定用的法規名稱
--
-- 停止適用的不變式全由約束表達，不靠載入者自律，也不靠觸發器：
-- - 停止日不得早於本件與後函的發文日。後函廢止的生效日可以晚於後函發文日（公報「廢止「甲」，
--   並自某日生效」），所以 0029 以 (by_document_id, ceased_on) 參照後函 (id, issued_on) 的作法
--   改為：兩個發文日以複合外鍵抄進本列，再以 CHECK 比較。
-- - cause 說明停止從何而來，並決定哪些欄位必填：successor（後函變更或廢止，釋字第 287 號）、
--   announced（來源載明停止之日，如函釋網站的「廢/停」）、taken_down（網站下架）。
-- - 下架的停止適用參照下架的那個來源項目：(feed, key, dropped_on) 必須是一個已下架的函釋項目，
--   key 是本件的發文字號、停止日就是下架日。
--
-- 函釋網站是爬取的全量（02-05 §2）：連續兩次完整走訪都沒出現的函釋推論為下架。missed_crawls 是
-- 連續幾次完整走訪沒有出現；下架是終態（停止適用只增不刪），之後再出現也不改，只記匯入問題。
-- 計數與下架只經 guidance.settle_crawl 轉移。
--
-- guidance.law_name：錨定時法規名稱 → pcode。名稱是鍵，所以一個名稱至多對到一部法規；
-- 對到兩部以上（標題重複、舊名與另一部的標題相同）的名稱 pcode 為空值，表示有歧義、不錨定。

CREATE TYPE guidance.cessation_cause AS ENUM ('successor', 'announced', 'taken_down');

ALTER TABLE guidance.document ADD UNIQUE (id, document_no);

ALTER TABLE source.item
    ADD COLUMN missed_crawls smallint NOT NULL DEFAULT 0 CHECK (missed_crawls BETWEEN 0 AND 2),
    ADD CHECK (feed = 'moenv_guidance' OR missed_crawls = 0),
    ADD CONSTRAINT dropped_after_two_missed_crawls
        CHECK (feed <> 'moenv_guidance' OR (dropped_on IS NOT NULL) = (missed_crawls = 2)),
    ADD UNIQUE (feed, key, dropped_on);

COMMENT ON COLUMN source.item.missed_crawls IS
    '爬取的全量來源（moenv_guidance）：連續幾次完整走訪沒有出現，2 即下架；其他來源恆為 0。';

DROP TRIGGER cessation_after_issue ON guidance.cessation;
DROP FUNCTION guidance.ceases_after_issue();

ALTER TABLE guidance.cessation
    DROP CONSTRAINT cessation_by_document_id_ceased_on_fkey,
    ADD COLUMN cause guidance.cessation_cause,
    ADD COLUMN issued_on date,
    ADD COLUMN by_issued_on date,
    ADD COLUMN dropped_feed source.feed,
    ADD COLUMN dropped_key text;

UPDATE guidance.cessation AS c
   SET cause = CASE WHEN c.by_document_id IS NULL THEN 'announced' ELSE 'successor' END::guidance.cessation_cause,
       issued_on = (SELECT issued_on FROM guidance.document WHERE id = c.document_id),
       by_issued_on = (SELECT issued_on FROM guidance.document WHERE id = c.by_document_id);

ALTER TABLE guidance.cessation
    ALTER COLUMN cause SET NOT NULL,
    ALTER COLUMN issued_on SET NOT NULL,
    ADD FOREIGN KEY (document_id, issued_on) REFERENCES guidance.document (id, issued_on)
        ON DELETE CASCADE,
    ADD FOREIGN KEY (by_document_id, by_issued_on) REFERENCES guidance.document (id, issued_on)
        ON DELETE CASCADE,
    ADD FOREIGN KEY (document_id, dropped_key) REFERENCES guidance.document (id, document_no)
        ON DELETE CASCADE,
    ADD CONSTRAINT taken_down_on_drop FOREIGN KEY (dropped_feed, dropped_key, ceased_on)
        REFERENCES source.item (feed, key, dropped_on),
    ADD CHECK ((by_document_id IS NULL) = (by_issued_on IS NULL)),
    ADD CHECK ((dropped_key IS NULL) = (dropped_feed IS NULL)),
    ADD CHECK (dropped_feed = 'moenv_guidance'),
    ADD CONSTRAINT successor_names_the_rule CHECK ((cause = 'successor') = (by_document_id IS NOT NULL)),
    ADD CONSTRAINT taken_down_names_the_item CHECK ((cause = 'taken_down') = (dropped_key IS NOT NULL)),
    ADD CONSTRAINT ceases_after_issue CHECK (ceased_on >= issued_on),
    ADD CONSTRAINT ceases_after_successor CHECK (ceased_on >= by_issued_on);

COMMENT ON COLUMN guidance.cessation.issued_on IS '本件的發文日（外鍵抄來，供 CHECK 比較）。';
COMMENT ON COLUMN guidance.cessation.by_issued_on IS '後函的發文日（外鍵抄來，供 CHECK 比較）。';

-- 一次完整走訪之後的狀態轉移。p_seen 是這次走訪出現的項目鍵；沒出現的超過還在架上的 5%，
-- 視為網站或走訪出錯，不轉移（complete 為假）。下架的函釋（來源網址以 p_source 開頭、發文字號
-- 即項目鍵）自下架日起停止適用；已記有停止適用的不再寫。
CREATE FUNCTION guidance.settle_crawl(p_seen text[], p_on date, p_source text)
RETURNS TABLE(complete boolean, dropped integer, ceased integer, reappeared bigint[])
LANGUAGE plpgsql
AS $$
DECLARE
    v_active integer;
    v_missing integer;
    v_dropped text[];
BEGIN
    SELECT count(*), count(*) FILTER (WHERE NOT i.key = ANY(p_seen))
      INTO v_active, v_missing
      FROM source.item AS i WHERE i.feed = 'moenv_guidance' AND i.dropped_on IS NULL;
    IF v_missing > 0.05 * v_active THEN
        RETURN QUERY SELECT false, 0, 0, '{}'::bigint[];
        RETURN;
    END IF;
    UPDATE source.item SET missed_crawls = 0
     WHERE feed = 'moenv_guidance' AND dropped_on IS NULL AND key = ANY(p_seen);
    WITH missed AS (
        UPDATE source.item
           SET missed_crawls = missed_crawls + 1,
               dropped_on = CASE WHEN missed_crawls + 1 = 2 THEN p_on END
         WHERE feed = 'moenv_guidance' AND dropped_on IS NULL AND NOT key = ANY(p_seen)
        RETURNING key, dropped_on
    )
    SELECT coalesce(array_agg(key) FILTER (WHERE dropped_on IS NOT NULL), '{}') INTO v_dropped
      FROM missed;
    RETURN QUERY
    WITH ceased AS (
        INSERT INTO guidance.cessation
            (document_id, issued_on, ceased_on, cause, dropped_feed, dropped_key)
        SELECT d.id, d.issued_on, p_on, 'taken_down', 'moenv_guidance', d.document_no
          FROM guidance.document AS d
         WHERE d.document_no = ANY(v_dropped) AND starts_with(d.source_url, p_source)
        ON CONFLICT (document_id) DO NOTHING
        RETURNING 1
    )
    SELECT true, cardinality(v_dropped), (SELECT count(*)::integer FROM ceased),
           (SELECT coalesce(array_agg(i.id ORDER BY i.id), '{}') FROM source.item AS i
             WHERE i.feed = 'moenv_guidance' AND i.dropped_on IS NOT NULL AND i.key = ANY(p_seen));
END
$$;

-- 名稱比對只正規化全形半形與空白（02-05 §3.2）
CREATE FUNCTION guidance.name_key(p_name text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN regexp_replace(normalize(p_name, NFKC), '\s+', '', 'g');

-- 語料任一版本的標題，與語料沿革記載的更名（「（原名稱：甲；新名稱：乙）」，新名稱後的冒號偶爾
-- 誤植為分號）中的舊名。沿革是全國法規資料庫的原文，所以只有語料裡的法規有舊名。
CREATE VIEW guidance.law_name AS
WITH named AS (
    SELECT guidance.name_key(e.title) AS name, r.local_id AS pcode, false AS alias
      FROM corpus.legal_expression AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     WHERE r.id_scheme = 'moj_pcode'
    UNION
    SELECT guidance.name_key(m[1]), r.local_id, true
      FROM corpus.lifecycle_event AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     CROSS JOIN LATERAL regexp_matches(
            guidance.name_key(e.source_text), '原名稱:([^;)]+);新名稱[:;]', 'g') AS m
     WHERE r.id_scheme = 'moj_pcode' AND e.source_text LIKE '%原名稱%'
)
SELECT name,
       CASE WHEN count(DISTINCT pcode) = 1 THEN min(pcode) END AS pcode,
       bool_and(alias) AS alias
  FROM named
 GROUP BY name;

COMMENT ON VIEW guidance.law_name IS
    '錨定的法規名稱：name 唯一；pcode 為空值表示這個名稱對到兩部以上法規（不錨定）；'
    'alias 為真表示只出現在沿革的更名紀錄（舊名）。';

GRANT EXECUTE ON FUNCTION guidance.settle_crawl(text[], date, text)
    TO envcite_ontology_writer, envcite_ontology_reviewer;
GRANT SELECT ON guidance.law_name
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
