-- 0008_definitions_lifecycle_provenance — where the text came from, when a norm
-- stops existing, how it got amended, and what its words mean.
--
-- Statutes consulted for this migration, quoted rather than recalled:
--
--   中央法規標準法 §13  法規明定自公布或發布日施行者，自公布或發布之日起算至
--                      第三日起發生效力
--   §14                 法規特定有施行日期，或以命令特定施行日期者，自該特定日
--                      起發生效力
--   §21                 廢止事由四款，第四款為「同一事項已定有新法規」
--   §22 III             依前二項程序廢止之法規…自公布或發布之日起，算至第三日
--                      起失效
--   §23                 法規定有施行期限者，期滿當然廢止，不適用前條之規定
--
-- §22 III is the mirror image of §13 and uses the same arithmetic, so it uses the
-- same function. §23 is deliberately excluded from it — expiry of a stated term
-- takes effect on expiry, and the third-day rule does not apply. Two failure
-- modes are avoided by writing this down: computing the third day for a sunset,
-- and computing nothing for a repeal.

-- ---------------------------------------------------------------------------
-- 1. Provenance
-- ---------------------------------------------------------------------------
--
-- 全國法規資料庫 states of its own contents: 「本網站法規資料係由政府各機關提供
-- 之電子檔或書面文字登打製作，若與各法規主管機關之公布文字有所不同，仍以各法規
-- 主管機關之公布資料為準」 — the source we read from does not claim to be the
-- authoritative text. A system that cites articles must be able to say so, and
-- until now there was nowhere to record it.

CREATE TYPE text_authority AS ENUM (
    'gazette',      -- 公布文字（政府公報、總統府公報）— authoritative
    'official_db',  -- 官方資料庫，自承以公布文字為準
    'unverified'    -- anything else
);

ALTER TABLE legal_expression
    ADD COLUMN authority   text_authority,
    ADD COLUMN source_url  text CHECK (source_url <> ''),
    -- When we fetched it.
    ADD COLUMN retrieved_at timestamptz,
    -- The cut-off the source states for its own data — 全國法規資料庫 publishes
    -- 「法規整編資料截止日」 weekly. Distinct from retrieved_at: fetching today
    -- does not mean today's amendments are in it, and conflating the two makes a
    -- stale corpus look current.
    ADD COLUMN source_as_of date,
    ADD CONSTRAINT legal_expression_provenance_complete
        CHECK ((authority IS NULL) = (retrieved_at IS NULL));

COMMENT ON COLUMN legal_expression.authority IS
    'Whether this text is the promulgated wording or a transcription of it.';

-- Text nobody can vouch for. Not refused — an unverified text is still better
-- than no text — but an answer resting on one should say so, and a corpus whose
-- provenance was never recorded should not read as though it was.
CREATE VIEW expression_without_provenance AS
SELECT e.iri, e.resource_id, e.title, e.valid_from
  FROM legal_expression AS e
 WHERE e.authority IS NULL;

-- ---------------------------------------------------------------------------
-- 2. When a norm stops existing
-- ---------------------------------------------------------------------------
--
-- Not the same thing as its text being replaced. An amendment produces a new
-- legal_expression and the resource carries on; repeal ends the resource itself.
-- An earlier draft of this migration had 'superseded' here as a third kind of
-- ending — it does not belong: replacement of a text is an expression-level fact
-- that legal_expression.valid_to already carries. §21 iv ("同一事項已定有新法規")
-- is a ground for *repeal* by a different statute, which is `repealed_by` below.

CREATE TYPE termination_reason AS ENUM (
    'repealed',   -- §21、§22：廢止，公布日起算第三日失效
    'sunset',     -- §23：定有施行期限，期滿當然廢止
    'suspended'   -- 停止適用
);

ALTER TABLE legal_resource
    ADD COLUMN termination termination_reason,
    -- The norm that repealed this one, where §21 iv applies.
    ADD COLUMN repealed_by text REFERENCES legal_resource (id_local),
    -- Publication date of the repealing instrument. §22 III computes the end date
    -- from it; §23 does not use it at all.
    ADD COLUMN termination_promulgated date,

    -- A reason without an end date is an assertion with no date behind it.
    ADD CONSTRAINT legal_resource_termination_needs_end
        CHECK (termination IS NULL OR valid_to IS NOT NULL),

    -- §22 III, enforced rather than trusted to the importer. Same arithmetic as
    -- §13 and the same function, so the offset exists in exactly one place.
    --
    -- The final comparison is IS NOT DISTINCT FROM, not =. With `=`, a NULL
    -- valid_to makes the whole expression NULL, and a CHECK treats NULL as
    -- satisfied — the constraint would look enforced while letting the row
    -- through. Another constraint happens to require valid_to here, but a rule
    -- that holds only because a different rule exists is one refactor from silence.
    ADD CONSTRAINT legal_resource_repeal_takes_effect_on_third_day
        CHECK (termination IS DISTINCT FROM 'repealed'
               OR termination_promulgated IS NULL
               OR valid_to IS NOT DISTINCT FROM
                  effective_from_publication(termination_promulgated)),

    -- §23: expiry of a stated term is not a §22 repeal, so it has no promulgation
    -- date to compute from. Storing one would invite the third-day rule to be
    -- applied where the statute says it does not apply.
    ADD CONSTRAINT legal_resource_sunset_has_no_promulgation
        CHECK (termination IS DISTINCT FROM 'sunset' OR termination_promulgated IS NULL),

    -- IS NOT DISTINCT FROM, for the same reason: with `=`, naming a repealing
    -- statute while leaving `termination` NULL evaluates to NULL and is accepted.
    ADD CONSTRAINT legal_resource_repealed_by_needs_repeal
        CHECK (repealed_by IS NULL OR termination IS NOT DISTINCT FROM 'repealed'),

    ADD CONSTRAINT legal_resource_not_repealed_by_itself
        CHECK (repealed_by IS DISTINCT FROM id_local);

-- ---------------------------------------------------------------------------
-- 3. Amendments
-- ---------------------------------------------------------------------------

-- §13 vs §14: when a norm takes effect is either derived from its publication
-- date or stated outright. Which one applies is a fact about the instrument, and
-- recording it is what lets the derivation be checked.
CREATE TYPE commencement_rule AS ENUM (
    'third_day',  -- §13：自公布或發布之日起算至第三日
    'specified',  -- §14：法規特定有施行日期
    'by_order'    -- §14：以命令特定施行日期
);

CREATE TABLE amendment (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    promulgated date NOT NULL,
    effective   date NOT NULL,
    rule        commencement_rule NOT NULL,
    -- 公布字號, e.g. 華總一義字第11300000000號.
    gazette_ref text CHECK (gazette_ref <> ''),
    -- 立法理由／修正理由. What makes "why is it written this way" answerable.
    rationale   text CHECK (rationale <> ''),

    -- §13 made arithmetic instead of a convention. A CHECK can do this because
    -- both columns and the function are on this row — no other table is consulted,
    -- which is exactly the case a CHECK is allowed to handle.
    CONSTRAINT amendment_third_day_is_computed
        CHECK (rule <> 'third_day' OR effective = effective_from_publication(promulgated)),

    CONSTRAINT amendment_unique UNIQUE (resource_id, promulgated)
);

CREATE INDEX amendment_by_resource ON amendment (resource_id, effective);

CREATE TYPE change_kind AS ENUM ('added', 'amended', 'deleted');

CREATE TABLE amendment_change (
    amendment_id bigint NOT NULL REFERENCES amendment (id) ON DELETE CASCADE,
    -- The article number, not an iri: a deleted article has no new text and
    -- therefore no subdivision row in the version that removed it.
    number       text NOT NULL CHECK (number ~ '^[0-9]+(-[0-9]+)*$'),
    kind         change_kind NOT NULL,
    PRIMARY KEY (amendment_id, number)
);

-- ---------------------------------------------------------------------------
-- 4. Defined terms
-- ---------------------------------------------------------------------------
--
-- A definition binds within the statute that gives it. 「事業」 under 水污染防治法
-- and 「事業」 under 空氣污染防制法 are different things, and a global glossary
-- would merge them into one — silently, and in favour of whichever was imported
-- last.

CREATE TABLE term_definition (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    term       text NOT NULL CHECK (term <> ''),
    -- The article stating the definition.
    defined_in text NOT NULL REFERENCES subdivision (iri) ON DELETE CASCADE,
    -- The statute the definition binds within. Usually the one containing
    -- `defined_in` (「本法所稱」), but an enforcement rule defining a term of its
    -- parent statute scopes to the parent — so the two are not required to agree.
    scope_resource text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    -- No copy of the defining clause here. It is part of the article's text, and
    -- a second copy drifts with nothing to notice — the same reason 0006 keeps
    -- text on articles alone. Locating the clause precisely within the article is
    -- the same open question as locating a paragraph, and it stays open.

    CONSTRAINT term_definition_unique UNIQUE (term, defined_in)
);

CREATE INDEX term_definition_by_scope ON term_definition (scope_resource, term);

-- One term defined more than once within the same statute. Sometimes legitimate
-- (an enforcement rule elaborating on its parent), sometimes an extraction error.
-- Telling them apart needs a human, so this lists both rather than guessing —
-- and a unique constraint would have refused the legitimate case.
CREATE VIEW term_defined_more_than_once AS
SELECT t.term, t.scope_resource, count(*) AS definitions
  FROM term_definition AS t
 GROUP BY t.term, t.scope_resource
HAVING count(*) > 1;

-- Effective before promulgated. Usually an import error, occasionally real —
-- retroactive commencement is rare but not unlawful, and a CHECK refusing it
-- would make correct data look like a bug while leaving nothing to inspect.
CREATE VIEW amendment_retroactive AS
SELECT a.id, a.resource_id, a.promulgated, a.effective, a.rule
  FROM amendment AS a
 WHERE a.effective < a.promulgated;

-- Articles marked as definitions that state no defined term. Either the role is
-- wrong or the extraction has not happened — both are worth seeing.
CREATE VIEW definition_article_without_term AS
SELECT d.iri, d.resource_id, d.number
  FROM subdivision AS d
  JOIN subdivision_role AS r ON r.subdivision_iri = d.iri AND r.role = 'definition'
 WHERE NOT EXISTS (
        SELECT 1 FROM term_definition AS t WHERE t.defined_in = d.iri
   );
