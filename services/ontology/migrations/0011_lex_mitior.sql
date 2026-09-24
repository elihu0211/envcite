-- 0011_lex_mitior — the versions between the act and the disposition.
--
-- 行政罰法 §5：行為後法律或自治條例有變更者，適用裁處時之法律或自治條例。
-- 但裁處前之法律或自治條例有利於受處罰者，適用最有利於受處罰者之規定。
--
-- Three things about that sentence shape what this migration does and does not
-- do, and each of them is easy to get wrong by quoting the rule from memory
-- rather than from the statute:
--
--   1. It says 「法律或自治條例」 — statutes and local ordinances. It does NOT
--      say 法規命令. Yet in environmental enforcement almost every operative
--      figure — emission limits, reporting deadlines, size thresholds — sits in
--      a delegated regulation. Whether §5 reaches them is contested. **This
--      migration takes no position.** It returns the norm kind and lets the
--      caller scope the rule according to the view it holds. Deciding here would
--      bury a contested legal position inside a query.
--
--   2. It says 「裁處時」, not 「最初裁處時」. The prevailing practice reads it as
--      the first disposition, so that an authority cannot move the reference
--      point by re-issuing; but that is interpretation, not text. The caller
--      supplies the date.
--
--   3. 中央法規標準法 §18 is a different rule, not the same one. It governs
--      applications by the public for permits (「各機關受理人民聲請許可案件」)
--      and its reference point is the end of processing, not disposition. The
--      two are commonly called by one name, and conflating them applies the
--      permit rule to penalties.
--
-- **What this does not do: decide which version is more favourable.** That is a
-- legal judgement — it weighs fine ranges against other effects, and a lower
-- maximum is not automatically 「有利」. The database enumerates the candidates
-- completely and stops there. A function that returned "the applicable version"
-- would be making that judgement silently, on behalf of whoever read it.

-- Not named `applicable_versions`: nothing here determines applicability. The
-- name has to survive being read by someone in a hurry.
CREATE FUNCTION candidate_versions(
    p_resource         text,
    p_language         text,
    -- 行為時
    p_act_date         date,
    -- 裁處時（通說採最初裁處時，但那是解釋；此處照呼叫端所給）
    p_disposition_date date
)
RETURNS TABLE (
    expression_iri          text,
    valid_from              date,
    valid_to                date,
    version_label           text,
    -- 規範種類。§5 的文義只及於法律與自治條例，而是否及於法規命令有爭議
    -- ——回傳它，讓呼叫端依所採見解界定範圍。
    kind                    norm_kind,
    in_force_at_act         boolean,
    in_force_at_disposition boolean
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    IF p_act_date > p_disposition_date THEN
        -- Not an empty result: an act after its own disposition is incoherent
        -- input, and returning nothing would look like "no versions found".
        RAISE EXCEPTION 'act date % is after disposition date %',
            p_act_date, p_disposition_date
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM legal_resource AS l WHERE l.id_local = p_resource) THEN
        -- Distinguishable from "held, but no version in that window" — the same
        -- distinction 0004 draws, for the same reason.
        RAISE EXCEPTION 'unknown law %', p_resource
            USING ERRCODE = 'no_data_found';
    END IF;

    RETURN QUERY
    SELECT e.iri,
           e.valid_from,
           e.valid_to,
           e.version_label,
           l.kind,
           daterange(e.valid_from, e.valid_to, '[)') @> p_act_date,
           daterange(e.valid_from, e.valid_to, '[)') @> p_disposition_date
      FROM legal_expression AS e
      JOIN legal_resource AS l ON l.id_local = e.resource_id
     WHERE e.resource_id = p_resource
       AND e.language = p_language
       -- Every text whose validity overlaps the window, inclusive of both ends:
       -- a version in force only on the disposition date is squarely within §5,
       -- and one in force only on the act date is the comparison baseline.
       AND daterange(e.valid_from, e.valid_to, '[)')
           && daterange(p_act_date, p_disposition_date, '[]')
     ORDER BY e.valid_from NULLS FIRST, e.iri;
END;
$$;

COMMENT ON FUNCTION candidate_versions IS
    'Versions overlapping [act, disposition]. Enumerates candidates for 行政罰法 §5; decides nothing.';

-- Laws that changed between an act and a disposition, in the corpus as held.
-- Its purpose is to make the question visible: a penalty consultation spanning
-- one of these must not be answered from a single version.
CREATE VIEW resource_amended_between AS
SELECT e.resource_id,
       l.kind,
       count(*) AS versions,
       min(e.valid_from) AS earliest_from,
       max(e.valid_from) AS latest_from
  FROM legal_expression AS e
  JOIN legal_resource AS l ON l.id_local = e.resource_id
 GROUP BY e.resource_id, l.kind
HAVING count(*) > 1;
