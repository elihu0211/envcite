-- 0009_retrieval — lexical and dense retrieval over article text, fused at the
-- level an answer cites.
--
-- Three decisions that shape everything below.
--
-- **The unit is the article.** Not a fixed-size window. An answer cites 「空氣污染
-- 防制法第二十條」, so a hit that cannot be named by article number cannot be
-- cited. Chunking exists to fit small context windows; modern embedding models
-- have context far larger than any article, so the only thing chunking would buy
-- here is citations that do not resolve.
--
-- **Both arms recall the same unit, so fusion is free.** The BM25 index and the
-- vector index are on the same rows. Hybrid retrieval usually founders on the two
-- arms returning different granularities and needing a mapping step that loses
-- information; there is nothing to map here.
--
-- **Structure filters before either arm runs.** The corpus subgraph (0003), the
-- version in force on a date (0004) and the role of a norm (0006) are all filters
-- on the same rows, in the same query, in the same snapshot. This is what putting
-- them in one database buys, and it is not available to an architecture where the
-- graph, the text and the vectors live in three services: there, `as_of` is
-- applied twice against two different points in time.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;

-- ---------------------------------------------------------------------------
-- Vectors
-- ---------------------------------------------------------------------------

-- The dimension is part of the type, and changing it means recomputing every
-- vector and rebuilding the index. Naming it once makes that a single edit
-- instead of a search-and-replace that misses one.
--
-- 1024 is chosen for not committing to a model: it is the native width of both
-- bge-m3 and Qwen3-Embedding-0.6B, and the larger Qwen3 models support Matryoshka
-- truncation to it. A width that only one model produces would make the choice of
-- model irreversible without a full re-index.
CREATE DOMAIN article_vector AS vector(1024);

ALTER TABLE subdivision
    ADD COLUMN embedding article_vector,
    -- Which model produced it. Not documentation: vectors from two models are not
    -- comparable, and the distance between them is a number with no meaning — one
    -- that ranks results perfectly plausibly. Retrieval filters on this.
    ADD COLUMN embedding_model text CHECK (embedding_model <> ''),

    ADD CONSTRAINT subdivision_embedding_names_its_model
        CHECK ((embedding IS NULL) = (embedding_model IS NULL)),

    -- A vector with no text behind it cannot be correct, whatever it ranks.
    ADD CONSTRAINT subdivision_embedding_needs_text
        CHECK (embedding IS NULL OR content IS NOT NULL);

-- Derived, so it cannot disagree with parent_iri. It exists because the ParadeDB
-- index can filter on a column but not on a comparison between two columns, and
-- restricting retrieval to articles is the most common filter there is.
ALTER TABLE subdivision
    ADD COLUMN is_article boolean GENERATED ALWAYS AS (parent_iri IS NULL) STORED;

-- HNSW rather than IVFFlat: IVFFlat must be built on representative data, which
-- does not exist yet, while HNSW can be created on an empty table and stays
-- correct as rows arrive. At this corpus size an exact scan would also be fine —
-- the index is here so that growth does not require a schema change, and the
-- iterative scan settings on the search function below keep filtering from
-- eroding recall, which is the failure an approximate index brings with it.
CREATE INDEX subdivision_embedding_hnsw ON subdivision
    USING hnsw ((embedding::vector(1024)) vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Lexical
-- ---------------------------------------------------------------------------

-- Lindera with the Chinese dictionary segments into words rather than characters.
-- Character-level tokenisation of Chinese makes 「污染」 match 「污」 in unrelated
-- compounds, which is not a ranking problem but a wrong-document problem.
--
-- The filter columns are in the index deliberately: ParadeDB can then apply them
-- inside the index scan instead of after it. Filtering after an index scan is
-- what makes a hybrid query quietly return fewer results than it should.
CREATE INDEX subdivision_search ON subdivision
USING paradedb (
    iri,
    (content::pdb.lindera(chinese)),
    resource_id,
    expression_iri,
    article_iri,
    is_article
) WITH (key_field = 'iri');

-- ---------------------------------------------------------------------------
-- Search
-- ---------------------------------------------------------------------------

-- Reciprocal Rank Fusion (Cormack, Clarke & Buettcher, 2009). k = 60 is the
-- constant from that paper and is not tuned for this corpus.
--
-- Both arms carry weight 1.0. Published examples often show an asymmetric pair
-- such as 1.0/0.7; those are illustrative values, and adopting one would state a
-- measured preference we have not measured. The weights are parameters so that a
-- future measurement can set them — and so that the current setting is visibly
-- "no preference" rather than a number someone will later cite as tuning.
CREATE FUNCTION search_articles(
    p_query           text,
    p_query_vector    article_vector,
    p_embedding_model text,
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
    lexical AS (
        SELECT d.iri,
               RANK() OVER (ORDER BY pdb.score(d.iri) DESC, d.iri)::integer AS rank,
               pdb.score(d.iri) AS bm25
          FROM subdivision AS d
         WHERE d.content ||| p_query
           AND d.iri IN (SELECT c.iri FROM candidate AS c)
         ORDER BY pdb.score(d.iri) DESC, d.iri
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
           -- model, and we have measured none. A borrowed constant would look
           -- like calibration. Without it the arm returns its nearest neighbours
           -- however far away they are — which is correct behaviour for a
           -- retrieval primitive and wrong behaviour for an answer, so deciding
           -- answerability belongs to the caller, with the columns above.
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
           f.score, l.rank, v.rank, l.bm25, v.distance
      FROM fused AS f
      JOIN subdivision AS d ON d.iri = f.iri
      LEFT JOIN lexical AS l ON l.iri = f.iri
      LEFT JOIN dense AS v ON v.iri = f.iri
     ORDER BY f.score DESC, d.iri
     LIMIT p_limit;
$$;

COMMENT ON FUNCTION search_articles IS
    'Hybrid retrieval fused at the article level, filtered by corpus subgraph and by the version in force on a date.';

-- ---------------------------------------------------------------------------
-- Gaps, enumerable
-- ---------------------------------------------------------------------------

-- Articles with text but no vector: the dense arm cannot see them, and a query
-- that should have found them returns a plausible list without them.
CREATE VIEW article_missing_embedding AS
SELECT d.iri, d.resource_id, d.number, d.expression_iri
  FROM subdivision AS d
 WHERE d.is_article
   AND d.content IS NOT NULL
   AND d.embedding IS NULL;

-- Which models the corpus currently holds vectors for. More than one row means
-- retrieval is silently partitioned: each query sees only the articles embedded
-- with the model it asked for.
CREATE VIEW embedding_model_in_use AS
SELECT d.embedding_model, count(*) AS articles
  FROM subdivision AS d
 WHERE d.embedding_model IS NOT NULL
 GROUP BY d.embedding_model;
