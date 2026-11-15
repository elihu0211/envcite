-- 0031_subtract — 拿掉沒有生產者的列舉值
--
-- corpus.norm_kind 的 administrative_rule：行政規則記在 guidance（0029），沒有條、項、款，不進語料，
-- 匯入也從不產生這個值。留著會讓人以為語料裡有行政規則。已有列用到時本遷移失敗。
-- PostgreSQL 不能刪除列舉值，所以重建型別；依賴它的 resource_iri 與依賴 resource_iri 的
-- version_iri（以 SQL 標準本體存放，記錄依賴）先刪後建，內容不變。

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM corpus.legal_resource WHERE norm_kind = 'administrative_rule') THEN
        RAISE EXCEPTION 'legal_resource rows use norm_kind administrative_rule; administrative rules belong in guidance'
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

DROP FUNCTION corpus.version_iri(bigint);
DROP FUNCTION corpus.resource_iri(corpus.norm_kind, text);

CREATE TYPE corpus.norm_kind_0031 AS ENUM
    ('constitution', 'statute', 'regulation', 'local_ordinance', 'local_regulation');
ALTER TABLE corpus.legal_resource
    ALTER COLUMN norm_kind TYPE corpus.norm_kind_0031 USING norm_kind::text::corpus.norm_kind_0031;
DROP TYPE corpus.norm_kind;
ALTER TYPE corpus.norm_kind_0031 RENAME TO norm_kind;

CREATE FUNCTION corpus.resource_iri(kind corpus.norm_kind, local_id text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE STRICT
RETURN ((('https://envcite/tw/'::text || CASE WHEN (kind = ANY (ARRAY['constitution'::corpus.norm_kind, 'statute'::corpus.norm_kind])) THEN 'law'::text ELSE 'order'::text END) || '/'::text) || local_id);

CREATE FUNCTION corpus.version_iri(p_version bigint)
 RETURNS text
 LANGUAGE sql
 STABLE STRICT
RETURN (SELECT ((((((corpus.resource_iri(r.norm_kind, r.local_id) || '/'::text) || e.version_date) || '/'::text) || e.language) || '/'::text) || p.path) FROM (((corpus.provision_version v JOIN corpus.provision p ON ((p.id = v.provision_id))) JOIN corpus.legal_expression e ON ((e.id = v.expression_id))) JOIN corpus.legal_resource r ON ((r.id = v.resource_id))) WHERE (v.id = version_iri.p_version));
