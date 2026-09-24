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
-- **Both arms will recall the same unit, so fusion is free.** The lexical index
-- (0010) and the vector index are on the same rows. Hybrid retrieval usually
-- founders on the two arms returning different granularities and needing a
-- mapping step that loses information; there is nothing to map here.
--
-- **Structure filters before either arm runs.** The corpus subgraph (0003), the
-- version in force on a date (0004) and the role of a norm (0006) are all filters
-- on the same rows, in the same query, in the same snapshot. This is what putting
-- them in one database buys, and it is not available to an architecture where the
-- graph, the text and the vectors live in three services: there, `as_of` is
-- applied twice against two different points in time.

-- 🚨 This migration originally created a ParadeDB (`pg_search`) index and a
-- search function written against it; 0010 replaced both. All of it has been
-- removed from here, because a `CREATE EXTENSION pg_search` that no environment
-- can satisfy fails before 0010 ever runs — and because a SQL function body is
-- parsed at CREATE time, so `pdb.score(...)` fails there too.
--
-- What remains here is the dense arm alone. `search_articles()` now appears for
-- the first time in 0010, where both arms exist to be fused.
--
-- Editing an applied migration is normally forbidden — the checksums in
-- `migrate.py` exist to forbid it. It is legitimate exactly once, and this is
-- that once: **no database has ever applied these migrations**. The cluster does
-- not exist yet. Once one does, this kind of tidying stops being available and
-- the chain becomes append-only. The history of the decision lives in the commits.

CREATE EXTENSION IF NOT EXISTS vector;

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
