-- 0004_temporal_model — versions, article hierarchy, and point-in-time resolution.
--
-- What 0001 got wrong and this fixes:
--
--   `legal_expression` was UNIQUE (resource_id, language), which permits exactly
--   one text per law per language — i.e. no amendment history at all. In ELI the
--   expression IS the version-bearer, so the uniqueness has to move to the time
--   dimension instead.
--
--   `subdivision` pointed at the resource, so an article belonged to the abstract
--   norm rather than to a specific text. Article 5 of the 2020 text and Article 5
--   of the 2024 text are different text under the same number; hanging both off
--   the resource makes them collide.
--
-- Doing this now is cheap because no real data exists. It would not stay cheap.

-- Equality operators inside a GiST exclusion constraint come from btree_gist;
-- without it the constraint below cannot be created at all.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- §13：法規明定自公布或發布日施行者，自公布或發布之日起算至第三日起發生效力。
-- 起算日為公布日本身（第一日），故第三日為公布日加二。這個偏移只寫在這裡；匯入端
-- 呼叫它，不自行加減——手算的日期會在某一處少一天，而少的那一天沒有任何訊號。
CREATE FUNCTION effective_from_publication(published date) RETURNS date
    LANGUAGE sql IMMUTABLE STRICT
    RETURN published + 2;

ALTER TABLE legal_expression
    -- Half-open [valid_from, valid_to). NULL upper bound means "still in force".
    ADD COLUMN valid_from date,
    ADD COLUMN valid_to   date,
    -- As published, e.g. '2024-08-07 修正'. Display only; never a sort key.
    ADD COLUMN version_label text,
    ADD CONSTRAINT legal_expression_validity_ordered
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to);

-- One law, one language, one text in force at a time. An overlap is not a query
-- that needs disambiguating — it is a contradiction, and the database refuses it.
ALTER TABLE legal_expression
    DROP CONSTRAINT legal_expression_resource_id_language_key,
    ADD CONSTRAINT legal_expression_no_overlap
        EXCLUDE USING gist (
            resource_id WITH =,
            language    WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&
        );

-- Articles belong to a text, not to the abstract norm.
ALTER TABLE subdivision
    -- No single-column FK here: the composite one added below carries it, and two
    -- foreign keys to the same table invite one of them being dropped as redundant.
    ADD COLUMN expression_iri text,
    -- 條 → 項 → 款. NULL parent means the subdivision hangs directly off the text.
    -- Composition, not reference: this is not a norm_relation.
    ADD COLUMN parent_iri text REFERENCES subdivision (iri) ON DELETE CASCADE,
    ADD CONSTRAINT subdivision_not_own_parent CHECK (parent_iri IS DISTINCT FROM iri);

-- `resource_id` stays as a denormalised shortcut for the common query, but it must
-- agree with the expression's resource. Left free, the two drift and neither is
-- obviously the wrong one.
--
-- Enforced by a composite foreign key, not by a CHECK: a CHECK may not contain a
-- subquery, and a CHECK calling a function that queries another table is documented
-- as unsupported — it is not re-evaluated when the other table changes, so it looks
-- enforced while silently letting the rows drift apart.
ALTER TABLE legal_expression
    ADD CONSTRAINT legal_expression_iri_resource_key UNIQUE (iri, resource_id);

ALTER TABLE subdivision
    ADD CONSTRAINT subdivision_resource_agrees
        FOREIGN KEY (expression_iri, resource_id)
        REFERENCES legal_expression (iri, resource_id) ON DELETE CASCADE;

-- Article numbers repeat across versions, so uniqueness moves to the text.
ALTER TABLE subdivision
    DROP CONSTRAINT subdivision_resource_id_number_key,
    ADD CONSTRAINT subdivision_number_unique_per_expression
        UNIQUE NULLS NOT DISTINCT (expression_iri, number);

CREATE INDEX subdivision_by_expression ON subdivision (expression_iri, sort_key);
CREATE INDEX subdivision_by_parent ON subdivision (parent_iri);

-- Point-in-time resolution.
--
-- Returns NULL when no text covers the date. That is the whole point: the corpus
-- may simply not hold the version in force then, and falling back to the current
-- text would produce an answer indistinguishable from a correct one. A caller that
-- gets NULL must say it does not know, not answer as of today.
CREATE FUNCTION expression_at(p_resource text, p_language text, p_on date)
    RETURNS text LANGUAGE sql STABLE
    RETURN (
        SELECT e.iri
          FROM legal_expression AS e
         WHERE e.resource_id = p_resource
           AND e.language = p_language
           AND daterange(e.valid_from, e.valid_to, '[)') @> p_on
         LIMIT 1
    );

-- The text in force now, per law and language. The common case, named so callers
-- do not each write their own "latest" and disagree about what latest means.
CREATE VIEW expression_current AS
SELECT e.*
  FROM legal_expression AS e
 WHERE e.valid_to IS NULL;
