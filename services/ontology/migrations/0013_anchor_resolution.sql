-- 0013_anchor_resolution — 標註的錨定解析：回報解析到的版本列與限值的來源陳述
--
-- 設計見 env-law-docs 02-04 §10、02-05 §5.3。
--
-- statements_at 只回陳述，依據軌跡要的是「陳述依哪個版本列、門檻值取自哪個限值陳述」：
-- 前者是版本的 IRI，後者是 thr_src。兩者都在解析時已經算出，不該由呼叫端再用同樣的連接重算
-- 一次（兩份邏輯遲早分岔），所以把解析寫成回報來源的函式，原有兩個函式改為它們的薄包裝。
-- 行為不變，既有測試即是回歸。

CREATE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (statement_id bigint, version_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT s.id, v.id
      FROM corpus.provisions_at(p_resource, p_language, p_on) AS a
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS s ON s.provision_id = v.provision_id
                                    AND s.content_sha256 = v.content_sha256
      JOIN annotation.derivation AS d ON d.id = s.derivation_id
     WHERE a.status = 'in_force'
       AND s.review <> 'rejected'
       AND (s.review = 'confirmed' OR d.method = 'rule')
$$;

CREATE OR REPLACE FUNCTION annotation.statements_at(p_resource bigint, p_language text, p_on date)
RETURNS SETOF annotation.statement
LANGUAGE sql STABLE
AS $$
    SELECT s.*
      FROM annotation.statement_versions_at(p_resource, p_language, p_on) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
$$;

-- 委任要件的門檻值與供給它的限值陳述。限值陳述取授權命令的單元在該日的現行列上、已確認的、
-- 同一概念的限值；解析不到則沒有列。
CREATE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
RETURNS TABLE (threshold numeric, statement_id bigint)
LANGUAGE sql STABLE
AS $$
    SELECT lim.threshold, ls.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
                                     AND ls.review = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
$$;

CREATE OR REPLACE FUNCTION annotation.threshold_at(p_condition bigint, p_language text, p_on date)
RETURNS numeric
LANGUAGE sql STABLE
AS $$
    SELECT coalesce(c.threshold, (SELECT s.threshold
                                    FROM annotation.threshold_source_at(c.id, p_language, p_on) AS s))
      FROM annotation.statement_condition AS c
     WHERE c.id = p_condition
$$;
