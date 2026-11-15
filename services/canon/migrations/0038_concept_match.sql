-- 0038_concept_match — 概念的外部對映，只記等同
--
-- 跨法對齊不靠代碼巧合：污染物有化學文摘社登記號（CAS 號），行業有行業統計分類代碼。兩個概念
-- 對到同一個外部識別碼，才視為同一個概念；推論據此把一方的事實當成另一方的事實。
--
-- 這是 0022「不借用 SKOS」的例外，只借「等同」：不記上下位、相關或近似，也不以 RDF 發佈。
-- 一個概念在一個外部體系裡只有一個識別碼（等同是函數，不是多對多的近似）；外部體系是開放的
-- 名稱，存為文字。對映是詞彙的一部分，隨概念刪除。

CREATE TABLE vocab.concept_match (
    concept_id      bigint NOT NULL REFERENCES vocab.concept (id) ON DELETE CASCADE,
    external_system text NOT NULL CHECK (external_system ~ '^[a-z][a-z0-9_]*$'),
    external_id     text NOT NULL CHECK (external_id <> '' AND external_id = btrim(external_id)),
    PRIMARY KEY (concept_id, external_system)
);

CREATE INDEX concept_match_by_external ON vocab.concept_match (external_system, external_id);

COMMENT ON TABLE vocab.concept_match IS
    '概念與外部識別碼（如 cas、行業統計分類）的等同；對到同一個外部識別碼的概念視為同一個概念。';
