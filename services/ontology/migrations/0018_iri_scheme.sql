-- 0018_iri_scheme — 法規識別碼樣板改成自己的路徑
--
-- 樣板原有一段 `eli`，是借用外國識別碼樣板的痕跡；本系統沒有外部消費者解析它，
-- 識別碼由自然鍵導出、不存（見 env-law-docs 03-02 §3），改樣板不動任何資料列。
-- 條文單元版本的 IRI 由 `corpus.version_iri` 接在法規 IRI 之後，隨之改變。

CREATE OR REPLACE FUNCTION corpus.resource_iri(kind corpus.norm_kind, local_id text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    RETURN 'https://env.law/tw/'
        || CASE WHEN kind IN ('constitution', 'statute') THEN 'law' ELSE 'order' END
        || '/' || local_id;
