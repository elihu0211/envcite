-- 0009_one_concept_per_name — 同一詞彙方案內，一個名稱只有一個概念
--
-- 抽取逐條進行，各條各自宣告概念，同一個意思常得到不同代碼、同一個中文名稱。概念的身分是
-- (方案, 名稱)：重複的列讓同一個事實對不上同一批要件。這裡把既有的重複合併成一列，並用唯一索引
-- 讓重複不可能再出現（載入器遇到同名就沿用既有概念）。
--
-- 合併改寫已封存的主張：它們把概念 id 算進內容指紋，合併後指紋要重算。這是一次性的識別碼正規化，
-- 內容的語意不變；封存的限制（content_after_seal）只在這個交易裡暫停，之後恢復。
--
-- - 只合併用法相同的：同為旗標，或同單位的數值。同名但一個是旗標、一個是數值，或單位不同，是不同
--   的概念（把「45」當日數與年數混在同一個概念上會算錯），後者改名加上用法以區分。
-- - 沒有任何主張用到、又有同名概念的列直接刪除，它們的別名與外部對映併到留下的那一列。
-- - 合併後內容完全相同的兩份主張只留一份（已確認者優先，其次先寫入者），另一份記為駁回。

CREATE TEMP TABLE _use ON COMMIT DROP AS
SELECT k.id, k.scheme_id, k.name, k.code,
       EXISTS (SELECT 1 FROM annotation.statement_condition AS c
                WHERE k.id IN (c.concept_id, c.value_concept_id, c.delegate_permit_concept_id,
                               c.measurement_method_concept_id, c.averaging_period_concept_id))
       OR EXISTS (SELECT 1 FROM annotation.statement_slot AS s WHERE s.concept_id = k.id)
       OR EXISTS (SELECT 1 FROM annotation.term_definition AS t WHERE t.term_concept_id = k.id)
       OR EXISTS (SELECT 1 FROM annotation.term_definition_member AS m
                   WHERE m.member_concept_id = k.id) AS used,
       (SELECT array_agg(DISTINCT coalesce(c.measure_unit, '')
                         ORDER BY coalesce(c.measure_unit, ''))
          FROM annotation.statement_condition AS c
         WHERE c.concept_id = k.id AND c.threshold IS NOT NULL
           AND NOT (c.comparator = 'eq' AND c.threshold = 1 AND c.measure_unit IS NULL)) AS units
  FROM vocab.concept AS k;

-- 用法：旗標，單一單位的數值，或單位不只一種（自成一類，不與任何概念合併）
ALTER TABLE _use ADD COLUMN shape text;
UPDATE _use
   SET shape = CASE WHEN units IS NULL THEN 'flag'
                    WHEN cardinality(units) = 1 THEN 'number:' || units[1]
                    ELSE 'mixed:' || id END;

-- 每個概念併到哪一列：用到的併到同方案、同名、同用法中最早的；沒用到的併到同名中最早用到的
CREATE TEMP TABLE _merge ON COMMIT DROP AS
SELECT id,
       CASE WHEN used
            THEN min(id) FILTER (WHERE used) OVER (PARTITION BY scheme_id, name, shape)
            ELSE coalesce(min(id) FILTER (WHERE used) OVER (PARTITION BY scheme_id, name),
                          min(id) OVER (PARTITION BY scheme_id, name))
       END AS keep
  FROM _use;
DELETE FROM _merge WHERE id = keep;

-- 同名但用法不同的，除最早用到的那一類之外，名稱加上用法；加完仍與別的名稱相撞的再加代碼
CREATE TEMP TABLE _rename ON COMMIT DROP AS
WITH odd AS (
    SELECT u.id, u.scheme_id, u.code, u.name,
           u.name || '（' || CASE WHEN u.shape = 'flag' THEN '旗標'
                                  WHEN u.shape = 'number:' THEN '數值'
                                  WHEN u.shape LIKE 'number:%' THEN substr(u.shape, 8)
                                  ELSE '多用法' END || '）' AS candidate
      FROM _use AS u
     WHERE u.used AND u.id NOT IN (SELECT id FROM _merge)
       AND u.id <> (SELECT min(o.id) FROM _use AS o
                     WHERE o.used AND o.scheme_id = u.scheme_id AND o.name = u.name)
       AND (SELECT count(DISTINCT o.shape) FROM _use AS o
             WHERE o.used AND o.scheme_id = u.scheme_id AND o.name = u.name) > 1
)
SELECT o.id,
       CASE WHEN count(*) OVER (PARTITION BY o.scheme_id, o.candidate) > 1
              OR EXISTS (SELECT 1 FROM _use AS t
                          WHERE t.scheme_id = o.scheme_id AND t.name = o.candidate
                            AND t.id NOT IN (SELECT id FROM _merge))
            THEN o.name || '（' || o.code || '）'
            ELSE o.candidate END AS new_name
  FROM odd AS o;

-- 別名與外部對映併到留下的那一列
INSERT INTO vocab.concept_label (concept_id, label, label_kind)
SELECT g.keep, l.label, l.label_kind
  FROM vocab.concept_label AS l JOIN _merge AS g ON g.id = l.concept_id
ON CONFLICT DO NOTHING;
INSERT INTO vocab.concept_match (concept_id, external_system, external_id)
SELECT g.keep, m.external_system, m.external_id
  FROM vocab.concept_match AS m JOIN _merge AS g ON g.id = m.concept_id
ON CONFLICT DO NOTHING;

-- 封存的內容暫時可改：只為把概念 id 換成留下的那一個
ALTER TABLE annotation.statement_condition DISABLE TRIGGER statement_condition_content_after_seal;
ALTER TABLE annotation.statement_slot DISABLE TRIGGER statement_slot_content_after_seal;
ALTER TABLE annotation.term_definition DISABLE TRIGGER term_definition_content_after_seal;
ALTER TABLE annotation.term_definition_member DISABLE TRIGGER term_definition_member_content_after_seal;

UPDATE annotation.statement_condition AS c SET concept_id = g.keep
  FROM _merge AS g WHERE c.concept_id = g.id;
UPDATE annotation.statement_condition AS c SET value_concept_id = g.keep
  FROM _merge AS g WHERE c.value_concept_id = g.id;
UPDATE annotation.statement_condition AS c SET delegate_permit_concept_id = g.keep
  FROM _merge AS g WHERE c.delegate_permit_concept_id = g.id;
UPDATE annotation.statement_condition AS c SET measurement_method_concept_id = g.keep
  FROM _merge AS g WHERE c.measurement_method_concept_id = g.id;
UPDATE annotation.statement_condition AS c SET averaging_period_concept_id = g.keep
  FROM _merge AS g WHERE c.averaging_period_concept_id = g.id;
UPDATE annotation.statement_slot AS s SET concept_id = g.keep
  FROM _merge AS g WHERE s.concept_id = g.id;
UPDATE annotation.term_definition AS t SET term_concept_id = g.keep
  FROM _merge AS g WHERE t.term_concept_id = g.id;

-- 同一個定義的成員合併後可能重複（主鍵是定義加成員）：重寫受影響的定義的成員
CREATE TEMP TABLE _members ON COMMIT DROP AS
SELECT DISTINCT m.definition_id, coalesce(g.keep, m.member_concept_id) AS member_concept_id
  FROM annotation.term_definition_member AS m
  LEFT JOIN _merge AS g ON g.id = m.member_concept_id
 WHERE m.definition_id IN (SELECT x.definition_id FROM annotation.term_definition_member AS x
                             JOIN _merge AS y ON y.id = x.member_concept_id);
DELETE FROM annotation.term_definition_member
 WHERE definition_id IN (SELECT definition_id FROM _members);
INSERT INTO annotation.term_definition_member (definition_id, member_concept_id)
SELECT definition_id, member_concept_id FROM _members;

-- 內容指紋重算；合併後內容相同的主張只留一份
CREATE TEMP TABLE _print ON COMMIT DROP AS
SELECT a.id, a.assertion_kind, a.confirm_state, a.fingerprint AS old_fp,
       annotation.assertion_fingerprint(a.id) AS new_fp
  FROM annotation.assertion AS a
 WHERE a.fingerprint IS NOT NULL;

UPDATE annotation.assertion AS a SET confirm_state = 'rejected'
  FROM (SELECT id, row_number() OVER (PARTITION BY assertion_kind, new_fp
                                      ORDER BY (confirm_state = 'confirmed') DESC, id) AS rn
          FROM _print WHERE confirm_state <> 'rejected') AS r
 WHERE a.id = r.id AND r.rn > 1;

UPDATE annotation.assertion AS a SET fingerprint = p.new_fp
  FROM _print AS p WHERE a.id = p.id AND p.old_fp IS DISTINCT FROM p.new_fp;

ALTER TABLE annotation.statement_condition ENABLE TRIGGER statement_condition_content_after_seal;
ALTER TABLE annotation.statement_slot ENABLE TRIGGER statement_slot_content_after_seal;
ALTER TABLE annotation.term_definition ENABLE TRIGGER term_definition_content_after_seal;
ALTER TABLE annotation.term_definition_member ENABLE TRIGGER term_definition_member_content_after_seal;

DELETE FROM vocab.concept WHERE id IN (SELECT id FROM _merge);
UPDATE vocab.concept AS k SET name = r.new_name FROM _rename AS r WHERE k.id = r.id;

CREATE UNIQUE INDEX concept_name_per_scheme ON vocab.concept (scheme_id, name);

COMMENT ON INDEX vocab.concept_name_per_scheme IS '概念的身分是（方案, 名稱）：同名即同一個概念，載入器遇到同名就沿用既有的。';
