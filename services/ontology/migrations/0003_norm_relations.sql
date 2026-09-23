-- 0003_norm_relations — norm kind, validity period, and the relations that define
-- the corpus boundary.
--
-- Why this exists (argument: env-law-docs 01-02): the corpus is NOT the set of
-- statutes administered by one ministry. The competent authority is an attribute
-- of administration, not of the norm: nothing in 中央法規標準法 uses it to decide
-- precedence, incorporation or validity. Scoping by it cuts the chain of
-- application in the middle — and 準用 (§17) crosses ministries, so the cut
-- produces no signal at all.
--
-- The boundary is instead the transitive closure of the relations below, from a
-- seed set. Its completeness is checkable from inside: every referenced-but-absent
-- statute is an enumerable gap.

-- Whether a norm may serve as the basis of a sanction is a property of its kind,
-- not of its author. 行政規則 (行政程序法 §159) has no external effect and may not
-- ground a penalty, yet it shares a competent authority with 法規命令.
CREATE TYPE norm_kind AS ENUM (
    'constitution',          -- 憲法
    'statute',               -- 法律：法、律、條例、通則（中央法規標準法 §2）
    'regulation',            -- 法規命令：規程、規則、細則、辦法、綱要、標準、準則（§3）
    'administrative_rule',   -- 行政規則（行政程序法 §159）— no external effect
    'local_ordinance',       -- 自治條例（地方制度法）
    'local_regulation'       -- 自治規則
);

-- Rank for §11 conflict resolution. Lower binds higher: a norm may not contradict
-- any norm of lower rank value. Derived from the kind, never written by hand —
-- a hand-written rank drifts from the kind it is supposed to describe.
CREATE FUNCTION norm_rank(kind norm_kind) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    RETURN CASE kind
        WHEN 'constitution'        THEN 1
        WHEN 'statute'             THEN 2
        WHEN 'local_ordinance'     THEN 3
        WHEN 'regulation'          THEN 4
        WHEN 'local_regulation'    THEN 5
        WHEN 'administrative_rule' THEN 6
    END;

ALTER TABLE legal_resource
    ADD COLUMN kind norm_kind NOT NULL DEFAULT 'statute',
    -- Administrative metadata only. It may be stale or empty; that must never
    -- remove a statute from the corpus. Filtering and display, nothing else.
    ADD COLUMN competent_authority text,
    -- Half-open [valid_from, valid_to). NULL upper bound means "still in force".
    -- §13: a norm that takes effect on publication does so on the third day
    -- counted from it — that offset is applied by the importer, which knows the
    -- publication date and the 施行 clause; this column holds the result.
    ADD COLUMN valid_from date,
    ADD COLUMN valid_to date,
    ADD CONSTRAINT legal_resource_validity_ordered
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_from < valid_to);

ALTER TABLE legal_resource ALTER COLUMN kind DROP DEFAULT;

COMMENT ON COLUMN legal_resource.competent_authority IS
    'Administrative metadata. Never a criterion for corpus membership (see docs 01-02).';

-- Relations between norms. Two time semantics live here and they are opposites,
-- so the distinction is carried explicitly rather than by convention:
--
--   'incorporates' (準用/適用, §17) points at the RESOURCE. After the target is
--   amended, the amended version applies. Pinning a version here would make the
--   system quietly keep applying the old one.
--
--   'cites' points at TEXT and is therefore version-bearing; `target_version`
--   records which version the citing text referred to.
CREATE TYPE relation_kind AS ENUM (
    'authorizes',    -- 母法 → 授權命令（行政程序法 §150、中央法規標準法 §5）
    'incorporates',  -- 準用／適用（§17）— dynamic
    'specialises',   -- 特別規定 → 其所特別於之普通規定（§16）
    'cites'          -- 條文互指 — version-bearing
);

CREATE TABLE norm_relation (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id      text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    target_id      text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    kind           relation_kind NOT NULL,
    -- Optional narrowing: which subdivision of the source carries the relation.
    -- NULL means "the norm as a whole", which is a distinct fact from any given
    -- subdivision carrying it — hence NULLS NOT DISTINCT below, so the whole-norm
    -- relation can be stated exactly once.
    source_iri     text REFERENCES subdivision (iri) ON DELETE CASCADE,
    -- Only meaningful for 'cites'; enforced below.
    target_version date,
    CONSTRAINT norm_relation_unique
        UNIQUE NULLS NOT DISTINCT (source_id, target_id, kind, source_iri),
    -- A norm cannot stand in any of these relations to itself.
    CONSTRAINT norm_relation_not_self CHECK (source_id <> target_id),
    -- Version pinning belongs to 'cites' alone. This is the §17 rule made
    -- unwritable rather than merely documented.
    CONSTRAINT norm_relation_version_only_for_cites
        CHECK (target_version IS NULL OR kind = 'cites')
);

CREATE INDEX norm_relation_outgoing ON norm_relation (source_id, kind);
CREATE INDEX norm_relation_incoming ON norm_relation (target_id, kind);

-- Unresolved references: a norm points at something the corpus does not hold.
--
-- This needs its own table, not a view over `norm_relation`. That table's
-- `target_id` is a foreign key, so a relation to an absent norm is not storable
-- there at all — and a gap that cannot be stored cannot be enumerated. Recording
-- the reference as text, separately, is what makes the boundary checkable from
-- inside: this is the property the competent-authority filter could not offer,
-- where a missing statute is invisible until compared against an external
-- population.
CREATE TABLE unresolved_reference (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id  text NOT NULL REFERENCES legal_resource (id_local) ON DELETE CASCADE,
    -- What the text points at, as written: a name or an identifier the importer
    -- could not resolve. Deliberately not a foreign key.
    target_ref text NOT NULL CHECK (target_ref <> ''),
    kind       relation_kind NOT NULL,
    source_iri text REFERENCES subdivision (iri) ON DELETE CASCADE,
    first_seen timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT unresolved_reference_unique
        UNIQUE NULLS NOT DISTINCT (source_id, target_ref, kind, source_iri)
);

CREATE INDEX unresolved_reference_by_target ON unresolved_reference (target_ref);

-- The gap list: what to fetch next, ordered by how many norms need it.
-- A reference resolves by the target arriving; the importer then moves the row
-- into `norm_relation`. Rows that never move are the corpus's real boundary.
CREATE VIEW corpus_gap AS
SELECT u.target_ref AS missing_ref,
       count(*)     AS referenced_by,
       min(u.first_seen) AS first_seen
  FROM unresolved_reference AS u
 WHERE NOT EXISTS (SELECT 1 FROM legal_resource AS l WHERE l.id_local = u.target_ref)
 GROUP BY u.target_ref;

-- §11 made checkable: an authorising norm must outrank what it authorises, and a
-- norm may not be special to something of lower rank. This is what `norm_rank`
-- exists for — a rank nothing consults is a number that drifts.
CREATE VIEW norm_hierarchy_violation AS
SELECT r.id AS relation_id, r.source_id, r.target_id, r.kind,
       s.kind AS source_kind, t.kind AS target_kind
  FROM norm_relation AS r
  JOIN legal_resource AS s ON s.id_local = r.source_id
  JOIN legal_resource AS t ON t.id_local = r.target_id
 WHERE r.kind IN ('authorizes', 'specialises')
   AND norm_rank(s.kind) >= norm_rank(t.kind);
