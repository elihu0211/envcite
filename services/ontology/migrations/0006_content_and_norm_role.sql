-- 0006_content_and_norm_role — the text of the law, and what each article does.
--
-- What was missing: the ELI layers had identifiers, titles, numbers, ordering,
-- validity and relations — but nowhere to put the text of an article. The same
-- shape occurred in the predecessor system, where the graph node for an article
-- carried no full text: every read returned an empty string and the obligation
-- cards degraded to titles. Structure without text is not a corpus.
--
-- This migration adds three things to `subdivision`, all of them structural and
-- all of them cheaper now than ever again:
--
--   1. `content`   — the text itself
--   2. `article_iri` — which article a subdivision belongs to, enforced
--   3. its role — obligation, definition, sanction … and what that permits

-- ---------------------------------------------------------------------------
-- 1. The text
-- ---------------------------------------------------------------------------

ALTER TABLE subdivision
    -- NULL means "not imported yet" and is enumerable (see the views below).
    -- The empty string is refused: "no text" and "text that is empty" are
    -- different facts, and collapsing them removes the only signal an importer
    -- gap would otherwise leave.
    ADD COLUMN content text CHECK (content <> '');

-- ---------------------------------------------------------------------------
-- 2. Which article a subdivision belongs to
-- ---------------------------------------------------------------------------
--
-- Retrieval and citation both work at the article level: an answer cites 「空氣
-- 污染防制法第二十條」, not a paragraph identifier. A hit on a nested paragraph
-- must therefore roll up to its article, and doing that with a recursive CTE per
-- query is both slow and easy to get subtly wrong.
--
-- The column would be a lie waiting to happen if nothing enforced it, so it is
-- enforced — not by a trigger and not by a CHECK (which may not query another
-- row), but by a composite foreign key that makes the invariant inductive:
--
--   * a top-level subdivision IS its own article            (CHECK below)
--   * any other subdivision has its parent's article_iri    (composite FK below)
--
-- Together these give, for every row, "article_iri is the root of my ancestor
-- chain" — by induction, with no code to run and nothing to keep in sync.

ALTER TABLE subdivision ADD COLUMN article_iri text;

-- Existing rows are all top-level, so each is its own article. This derives a
-- value for rows that already exist; it inserts nothing. Should a nested row
-- exist, the NOT NULL below fails and says so — which is the right outcome, as
-- the correct value would then be a decision, not a derivation.
UPDATE subdivision SET article_iri = iri WHERE parent_iri IS NULL;

ALTER TABLE subdivision ALTER COLUMN article_iri SET NOT NULL;

ALTER TABLE subdivision
    ADD CONSTRAINT subdivision_article_is_root
        CHECK ((parent_iri IS NULL) = (article_iri = iri));

ALTER TABLE subdivision
    ADD CONSTRAINT subdivision_article_key UNIQUE (iri, article_iri);

-- The single-column FK on parent_iri is dropped rather than kept alongside: two
-- foreign keys to the same table invite one of them being removed as redundant,
-- and the one that looks redundant is the one carrying the invariant. This is
-- the same reasoning 0004 applied to expression_iri.
ALTER TABLE subdivision DROP CONSTRAINT subdivision_parent_iri_fkey;

ALTER TABLE subdivision
    ADD CONSTRAINT subdivision_parent_agrees
        FOREIGN KEY (parent_iri, article_iri)
        REFERENCES subdivision (iri, article_iri) ON DELETE CASCADE;

-- Only articles carry text, for now. A paragraph's text is a fragment of its
-- article's, and storing both produces two copies that drift with nothing to
-- notice. How paragraphs will be located within an article — offsets, or their
-- own text with the article's becoming a projection — is undecided, and this
-- constraint keeps the question open rather than answering it by accident.
-- Relaxing it is a deliberate act that forces the decision to be made.
ALTER TABLE subdivision
    ADD CONSTRAINT subdivision_only_articles_carry_text
        CHECK (content IS NULL OR parent_iri IS NULL);

CREATE INDEX subdivision_by_article ON subdivision (article_iri);

-- ---------------------------------------------------------------------------
-- 3. What an article does
-- ---------------------------------------------------------------------------

CREATE TYPE norm_role AS ENUM (
    'obligation',     -- 作為義務
    'prohibition',    -- 不作為義務
    'permission',     -- 許可、免除
    'definition',     -- 用語定義
    'authorization',  -- 授權訂定法規命令（中央法規標準法 §5、行政程序法 §150）
    'sanction',       -- 罰則
    'procedure',      -- 程序
    'application',    -- 適用、準用（§17）
    'transitional',   -- 過渡
    'commencement'    -- 施行日期（§13）
);

-- An article commonly holds more than one role — a paragraph imposing a duty and
-- the next defining its terms — so this is a relation, not a column.
CREATE TABLE subdivision_role (
    subdivision_iri text NOT NULL REFERENCES subdivision (iri) ON DELETE CASCADE,
    role            norm_role NOT NULL,
    PRIMARY KEY (subdivision_iri, role)
);

CREATE INDEX subdivision_role_by_role ON subdivision_role (role);

-- Whether a norm can serve as the major premise of a sanction is a property of
-- what it does, not of where it sits. A definition states what a word means; it
-- imposes nothing and cannot be violated. A procedural provision binds the
-- authority, not the regulated party.
--
-- This rule has been stated in prose before — "hold the answer when the evidence
-- is insufficient, rather than pressing a definition into service as the major
-- premise" — and prose does not refuse anything. Written here, something can.
CREATE FUNCTION may_ground_sanction(role norm_role) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    RETURN role IN ('obligation', 'prohibition');

-- ---------------------------------------------------------------------------
-- 4. Gaps, enumerable
-- ---------------------------------------------------------------------------
--
-- Same principle as corpus_gap in 0003: what the corpus does not yet hold must
-- be listable from inside. An import that silently covers 60% of articles and an
-- import that covers all of them look identical from any query that only reads
-- what is there.

CREATE VIEW article_missing_content AS
SELECT d.iri, d.resource_id, d.number, d.expression_iri
  FROM subdivision AS d
 WHERE d.parent_iri IS NULL
   AND d.content IS NULL;

CREATE VIEW article_missing_role AS
SELECT d.iri, d.resource_id, d.number, d.expression_iri
  FROM subdivision AS d
 WHERE d.parent_iri IS NULL
   AND NOT EXISTS (
        SELECT 1 FROM subdivision_role AS r WHERE r.subdivision_iri = d.iri
   );

-- Articles that can ground a sanction, as of a role assignment. Named here so
-- callers do not each re-derive it and disagree — the disagreement would be
-- invisible, since both versions return plausible articles.
CREATE VIEW article_groundable AS
SELECT DISTINCT d.iri, d.resource_id, d.number, d.expression_iri, d.content
  FROM subdivision AS d
  JOIN subdivision_role AS r ON r.subdivision_iri = d.iri
 WHERE d.parent_iri IS NULL
   AND may_ground_sanction(r.role);
