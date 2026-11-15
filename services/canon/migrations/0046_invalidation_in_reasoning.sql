-- 0046_invalidation_in_reasoning — 違憲失效進入陳述與限值的版本解析
--
-- 被宣告失效的條文版本自失效日起不得作為推論依據（0043）。失效依查詢日解析
-- （corpus.invalidations_at）：只有裁判作成時已施行的文字失效，裁判之後修正的新文字不受影響，
-- 所以排除的是（條文單元，版本）而不是條文單元。
--
-- - statement_versions_at 不再回失效版本上的陳述；被排除的陳述由 invalidated_statements_at 回報，
--   推論把它們連同裁判字號列在結果旁，呼叫端看得到依據為何缺席。
-- - partial（裁判只宣告條文的一部分）同樣排除：資料模型到項為止，分不出陳述讀的是哪一部分；
--   寧可不推，並標出 partial 讓人核對裁判原文。
-- - 授權的限值同理：被授權條文的版本失效，限值不再解析（threshold_source_at）。

CREATE OR REPLACE FUNCTION annotation.statement_versions_at(p_resource bigint, p_language text, p_on date)
 RETURNS TABLE(statement_id bigint, version_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT r.statement_id, r.version_id
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.assertion AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
       AND NOT EXISTS (SELECT 1 FROM corpus.invalidations_at(p_resource, p_language, p_on) AS i
                        WHERE i.version_id = r.version_id)
$function$;

-- 某部法規在某日因違憲失效而不得作為依據的陳述：已確認（或規則推導）、錨定的版本在該日現行，
-- 而該版本被宣告失效。一個版本可被數則裁判宣告失效，各一列。
CREATE FUNCTION annotation.invalidated_statements_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE(
    statement_id bigint,
    version_id   bigint,
    ruling_no    text,
    invalid_from date,
    partial      boolean
)
LANGUAGE sql STABLE
AS $$
    SELECT r.statement_id, r.version_id, i.ruling_no, i.invalid_from, ri.partial
      FROM annotation.anchored_statements_at(p_resource, p_language, p_on) AS r
      JOIN annotation.assertion AS c ON c.id = r.statement_id
      JOIN annotation.derivation AS d ON d.id = c.derivation_id
      JOIN corpus.invalidations_at(p_resource, p_language, p_on) AS i ON i.version_id = r.version_id
      JOIN corpus.ruling_invalidation AS ri ON ri.ruling_id = i.ruling_id
                                           AND ri.provision_id = i.provision_id
     WHERE c.confirm_state <> 'rejected'
       AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
     ORDER BY r.statement_id, i.invalid_from, i.ruling_no
$$;

CREATE OR REPLACE FUNCTION annotation.threshold_source_at(p_condition bigint, p_language text, p_on date)
 RETURNS TABLE(threshold numeric, statement_id bigint, condition_id bigint)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT lim.threshold, ls.id, lim.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
      JOIN annotation.assertion AS lc ON lc.id = ls.id AND lc.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.condition_role = 'limit'
                                                AND lim.concept_id = c.concept_id
     WHERE c.id = p_condition
       AND NOT EXISTS (SELECT 1 FROM corpus.invalidations_at(dp.resource_id, p_language, p_on) AS i
                        WHERE i.version_id = v.id)
$function$;
