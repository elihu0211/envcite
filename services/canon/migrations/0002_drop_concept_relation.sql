-- 0002_drop_concept_relation — 移除沒有生產者的跨法概念關係表
--
-- 本法內的「屬於」只由用語定義（annotation.term_definition）承擔；跨法只以外部識別碼等同
-- （vocab.concept_match）。受控詞彙之間的 broader 不具遞移性，也不得把子概念的量測值當成上位概念的值，
-- 因此 vocab.concept_relation 與依它推論的 concept_broader 一併移除。

DROP TABLE vocab.concept_relation;
DROP TYPE vocab.concept_relation_kind;
