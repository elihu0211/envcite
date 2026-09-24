-- 0010_builtin_fts — the lexical arm moves from ParadeDB to PostgreSQL's own
-- full-text search, with segmentation done outside the database.
--
-- Why, in one sentence: **ParadeDB Community does not physically replicate its
-- index**, so the lexical arm could not survive a failover and could not be
-- served by a replica, while a GIN index over tsvector replicates like any other
-- part of the database.
--
-- The full argument, since this reverses a decision made two migrations ago:
--
--   * ParadeDB's own documentation states the index 「does not get physically
--     replicated and won't be available on other nodes in a high availability
--     setup」, and that HA 「requires ParadeDB Enterprise」. A promoted standby
--     would have served the dense arm alone — a shorter result list that looks
--     like a complete one, with nothing raised.
--   * The mainstream shape of hybrid retrieval on PostgreSQL is pgvector plus the
--     built-in full-text search, fused with RRF. ParadeDB was the deviation, and
--     this is the deviation's cost arriving.
--   * RRF consumes ranks, not scores. BM25 ranks better than ts_rank_cd in the
--     abstract, but after fusion what survives is mostly the ordering, so the gap
--     narrows to much less than the gap in availability.
--   * Segmentation moves to the application, which means the dictionary can be a
--     Taiwanese one. ParadeDB's Lindera Chinese tokenizer uses CC-CEDICT — a
--     mainland dictionary, whose segmentation of terms like 「公私場所」 or
--     「固定污染源」 is not something we verified.
--
-- What this costs, stated plainly: the tokenizer used at index time and the one
-- used at query time must agree, and a drift between them degrades retrieval
-- without raising anything. That is the same hazard `embedding_model` already
-- handles, so it is handled the same way — recorded on the row, compared at
-- query time.

-- The ParadeDB index and its extension are not dropped here: they were removed
-- from 0009 instead. `CREATE EXTENSION pg_search` fails outright where the
-- extension is not installed, so it would have killed the chain before this file
-- ran. 0009 carries the note on why editing it was legitimate.

-- ---------------------------------------------------------------------------
-- 1. Segmented text
-- ---------------------------------------------------------------------------

ALTER TABLE subdivision
    -- Built by the importer from `content`, using the 'simple' configuration
    -- over externally segmented words. 'simple' rather than a language
    -- configuration: the words arrive already split, and no stemmer should touch
    -- them — 「排放」 must not be folded into anything.
    ADD COLUMN content_tsv tsvector,

    -- Which segmenter produced it, with its version. Not documentation: a query
    -- segmented by a different tokenizer produces different lexemes, matches
    -- fewer rows, and returns a shorter list that reads as a complete one.
    ADD COLUMN tokenizer text CHECK (tokenizer <> ''),

    ADD CONSTRAINT subdivision_tsv_names_its_tokenizer
        CHECK ((content_tsv IS NULL) = (tokenizer IS NULL)),

    ADD CONSTRAINT subdivision_tsv_needs_text
        CHECK (content_tsv IS NULL OR content IS NOT NULL);

-- A standard GIN index: it replicates, it survives failover, and a standby can
-- serve it. That sentence is the entire reason this migration exists.
CREATE INDEX subdivision_content_tsv ON subdivision USING gin (content_tsv);

-- ---------------------------------------------------------------------------
-- 2. Queries
-- ---------------------------------------------------------------------------

-- Segmented query terms to a tsquery, OR-joined.
--
-- OR rather than AND: the lexical arm feeds RRF, where a broad candidate set and
-- a good ordering beat a narrow set. Requiring every term would make the arm
-- return nothing for most real questions, and an arm that returns nothing does
-- not fuse — it silently reduces hybrid retrieval to dense-only.
--
-- Built by composing plainto_tsquery per word rather than by string-building a
-- to_tsquery argument: the former escapes its input, the latter would hand user
-- text to a parser.
CREATE FUNCTION terms_to_tsquery(terms text) RETURNS tsquery
    LANGUAGE sql IMMUTABLE STRICT
    RETURN coalesce(
        (
            SELECT string_agg(plainto_tsquery('simple', w)::text, ' | ')
              FROM unnest(string_to_array(btrim(terms), ' ')) AS w
             WHERE btrim(w) <> ''
               AND plainto_tsquery('simple', w)::text <> ''
        )::tsquery,
        ''::tsquery
    );

COMMENT ON FUNCTION terms_to_tsquery(text) IS
    'Space-separated segmented terms to an OR-joined tsquery. Input is escaped, not parsed.';

-- ---------------------------------------------------------------------------
-- 3. Search, rebuilt on the new lexical arm
-- ---------------------------------------------------------------------------

-- First appearance: 0009 builds the dense arm, and fusion needs both.
CREATE FUNCTION search_articles(
    -- Already segmented, space separated. The caller segments with the same
    -- tokenizer named in p_tokenizer; the database does not segment.
    p_query_terms     text,
    p_query_vector    article_vector,
    p_embedding_model text,
    p_tokenizer       text,
    p_as_of           date,
    p_seed            text DEFAULT NULL,
    p_max_depth       integer DEFAULT 3,
    p_limit           integer DEFAULT 10,
    p_candidates      integer DEFAULT 50,
    p_weight_lexical  numeric DEFAULT 1.0,
    p_weight_dense    numeric DEFAULT 1.0,
    p_rrf_k           integer DEFAULT 60,
    p_max_distance    real DEFAULT NULL
)
RETURNS TABLE (
    iri          text,
    resource_id  text,
    number       text,
    content      text,
    -- Fused rank score. **Not a relevance measure**: RRF uses only positions, so
    -- the top result of a corpus containing nothing relevant scores the same as
    -- the top result of one that does. Anything deciding "is there an answer
    -- here" must look at the two columns below, not at this one.
    score        numeric,
    lexical_rank integer,
    dense_rank   integer,
    -- The raw signals, so a caller can tell a near match from the nearest match.
    lexical_score real,
    dense_distance real
)
LANGUAGE sql STABLE
-- Applied for the duration of this function and restored afterwards. A plain SET
-- would leak into whatever connection PgBouncer hands out next; SET LOCAL would
-- need an explicit transaction the caller might not open. This form needs neither.
SET hnsw.iterative_scan = strict_order
SET hnsw.max_scan_tuples = 20000
AS $$
    WITH RECURSIVE scope AS (
        -- A NULL seed means the whole corpus; the recursive arm is then dead.
        SELECT l.id_local, 0 AS depth
          FROM legal_resource AS l
         WHERE p_seed IS NULL OR l.id_local = p_seed
        UNION
        SELECT r.target_id, s.depth + 1
          FROM scope AS s
          JOIN norm_relation AS r ON r.source_id = s.id_local
         WHERE p_seed IS NOT NULL
           AND s.depth < p_max_depth
           AND r.kind IN ('authorizes', 'incorporates', 'specialises')
    ),
    candidate AS (
        -- The text in force on p_as_of, and only articles. A law with no version
        -- covering that date contributes nothing — it does not fall back to the
        -- current text, which is the rule 0004 exists for.
        SELECT d.iri
          FROM scope AS s
          JOIN subdivision AS d ON d.resource_id = s.id_local
          JOIN legal_expression AS e ON e.iri = d.expression_iri
         WHERE d.is_article
           AND daterange(e.valid_from, e.valid_to, '[)') @> p_as_of
    ),
    q AS (
        SELECT terms_to_tsquery(p_query_terms) AS query
    ),
    lexical AS (
        SELECT d.iri,
               RANK() OVER (
                   ORDER BY ts_rank_cd(d.content_tsv, q.query) DESC, d.iri
               )::integer AS rank,
               ts_rank_cd(d.content_tsv, q.query) AS score
          FROM subdivision AS d
          CROSS JOIN q
         WHERE d.content_tsv @@ q.query
           -- Lexemes produced by a different segmenter are not the same lexemes.
           -- Matching across tokenizers returns fewer rows, not wrong ones, which
           -- is the harder failure to notice.
           AND d.tokenizer = p_tokenizer
           AND d.iri IN (SELECT c.iri FROM candidate AS c)
         ORDER BY ts_rank_cd(d.content_tsv, q.query) DESC, d.iri
         LIMIT p_candidates
    ),
    dense AS (
        SELECT d.iri,
               RANK() OVER (
                   ORDER BY d.embedding::vector(1024) <=> p_query_vector::vector(1024), d.iri
               )::integer AS rank,
               (d.embedding::vector(1024) <=> p_query_vector::vector(1024))::real AS distance
          FROM subdivision AS d
         WHERE d.iri IN (SELECT c.iri FROM candidate AS c)
           AND d.embedding IS NOT NULL
           -- Vectors from a different model are not comparable. Silently mixing
           -- them yields a ranking, which is worse than yielding nothing.
           AND d.embedding_model = p_embedding_model
           -- An optional ceiling on cosine distance. There is no default, and
           -- that is the point: any figure here is conditioned on the embedding
           -- model, and we have measured none.
           AND (p_max_distance IS NULL
                OR d.embedding::vector(1024) <=> p_query_vector::vector(1024) <= p_max_distance)
         ORDER BY d.embedding::vector(1024) <=> p_query_vector::vector(1024), d.iri
         LIMIT p_candidates
    ),
    fused AS (
        SELECT u.iri, sum(u.weight) AS score
          FROM (
                SELECT l.iri, p_weight_lexical / (p_rrf_k + l.rank) AS weight FROM lexical AS l
                UNION ALL
                SELECT v.iri, p_weight_dense / (p_rrf_k + v.rank) AS weight FROM dense AS v
               ) AS u
         GROUP BY u.iri
    )
    SELECT d.iri, d.resource_id, d.number, d.content,
           f.score, l.rank, v.rank, l.score, v.distance
      FROM fused AS f
      JOIN subdivision AS d ON d.iri = f.iri
      LEFT JOIN lexical AS l ON l.iri = f.iri
      LEFT JOIN dense AS v ON v.iri = f.iri
     ORDER BY f.score DESC, d.iri
     LIMIT p_limit;
$$;

COMMENT ON FUNCTION search_articles IS
    'Hybrid retrieval fused at the article level. Query terms arrive segmented; the database does not segment.';

-- ---------------------------------------------------------------------------
-- 4. Gaps, enumerable
-- ---------------------------------------------------------------------------

CREATE VIEW article_missing_tsv AS
SELECT d.iri, d.resource_id, d.number, d.expression_iri
  FROM subdivision AS d
 WHERE d.is_article
   AND d.content IS NOT NULL
   AND d.content_tsv IS NULL;

-- More than one row means the lexical arm is partitioned: a query segmented by
-- one tokenizer sees only the articles segmented by the same one.
CREATE VIEW tokenizer_in_use AS
SELECT d.tokenizer, count(*) AS articles
  FROM subdivision AS d
 WHERE d.tokenizer IS NOT NULL
 GROUP BY d.tokenizer;
