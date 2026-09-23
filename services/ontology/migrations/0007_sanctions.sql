-- 0007_sanctions — penalties, and the statutory amounts read out of the text.
--
-- The legal ground, checked against the statutes rather than recalled:
--
--   行政罰法 §18 I   裁處罰鍰應審酌責難程度、所生影響、所得之利益，並得考量資力
--   行政罰法 §18 II  所得之利益超過法定罰鍰最高額者，得於所得利益之範圍內酌量
--                    加重，不受法定罰鍰最高額之限制
--   行政罰法 §18 III 減輕時不得逾法定罰鍰最高額之二分之一，亦不得低於法定罰鍰
--                    最低額之二分之一
--   刑法 §10 I       稱以上、以下、以內者，俱連本數或本刑計算
--
-- Three consequences, each of which shapes a column below:
--
--   * What is stored is the STATUTORY range, not what anyone will be fined. §18
--     II lets the ceiling be exceeded outright and §18 III lets the floor be gone
--     below. The column names say so; `fine_max` would have read as a promise.
--   * The range is CLOSED at both ends (刑法 §10 I). A query for 「十萬元以上」
--     uses >=, not >. Getting this wrong is silent and only wrong at the boundary.
--   * An absent bound is NULL, never 0. 「處新臺幣六千元以下罰鍰」 states no
--     floor, and §18 III shows the floor is a legal concept with consequences —
--     a fabricated 0 would make the reduction rule compute against a number the
--     legislature never wrote.
--
-- And the reason the amounts are derived rather than typed: a hand-entered figure
-- drifts from the article it claims to describe, and the drift is silent until
-- somebody issues a penalty on it. Here the text is the truth and the numbers are
-- a projection of it, the same way sort_key projects number and norm_rank projects
-- kind. Display always uses `amount_text`; the numerics exist to filter and sort.

-- ---------------------------------------------------------------------------
-- Chinese numerals
-- ---------------------------------------------------------------------------

CREATE FUNCTION chinese_numeral(s text) RETURNS numeric
    LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    ch      text;
    d       numeric;
    current numeric := 0;   -- digit awaiting its place value
    section numeric := 0;   -- accumulated below 萬
    total   numeric := 0;   -- accumulated at 萬 and above
    seen    boolean := false;
BEGIN
    IF s = '' THEN
        RETURN NULL;
    END IF;

    -- Arabic form, with or without thousands separators: 「6,000」.
    IF s ~ '^[0-9][0-9,]*$' THEN
        RETURN replace(s, ',', '')::numeric;
    END IF;

    FOR i IN 1..length(s) LOOP
        ch := substring(s FROM i FOR 1);

        d := CASE ch
            WHEN '零' THEN 0 WHEN '〇' THEN 0
            WHEN '一' THEN 1 WHEN '壹' THEN 1
            WHEN '二' THEN 2 WHEN '貳' THEN 2 WHEN '兩' THEN 2
            WHEN '三' THEN 3 WHEN '參' THEN 3
            WHEN '四' THEN 4 WHEN '肆' THEN 4
            WHEN '五' THEN 5 WHEN '伍' THEN 5
            WHEN '六' THEN 6 WHEN '陸' THEN 6
            WHEN '七' THEN 7 WHEN '柒' THEN 7
            WHEN '八' THEN 8 WHEN '捌' THEN 8
            WHEN '九' THEN 9 WHEN '玖' THEN 9
            ELSE NULL
        END;

        IF d IS NOT NULL THEN
            current := d;
            seen := true;
            CONTINUE;
        END IF;

        CASE ch
            WHEN '十', '拾' THEN
                -- 「十萬」 begins with the place value: the implied digit is one.
                IF NOT seen THEN
                    current := 1;
                END IF;
                section := section + current * 10;
                current := 0;
                seen := true;
            WHEN '百', '佰' THEN
                IF NOT seen THEN
                    current := 1;
                END IF;
                section := section + current * 100;
                current := 0;
                seen := true;
            WHEN '千', '仟' THEN
                IF NOT seen THEN
                    current := 1;
                END IF;
                section := section + current * 1000;
                current := 0;
                seen := true;
            WHEN '萬' THEN
                total := total + (section + current) * 10000;
                section := 0;
                current := 0;
            WHEN '億' THEN
                total := total + (section + current) * 100000000;
                section := 0;
                current := 0;
            ELSE
                -- An unrecognised character means this is not a numeral we can
                -- read. NULL, not a guess: a wrong amount is worse than none.
                RETURN NULL;
        END CASE;
    END LOOP;

    RETURN total + section + current;
END;
$$;

COMMENT ON FUNCTION chinese_numeral(text) IS
    'Chinese numeral (or Arabic form) to numeric. NULL when unreadable — never a guess.';

-- ---------------------------------------------------------------------------
-- Reading the statutory range out of the text
-- ---------------------------------------------------------------------------

-- Returns {min, max}, either of which may be NULL when the article states only
-- one bound. Kept as one function so the two bounds cannot be read by two
-- patterns that disagree about what the sentence says.
CREATE FUNCTION fine_bounds(amount_text text) RETURNS numeric[]
    LANGUAGE plpgsql IMMUTABLE
AS $$
DECLARE
    -- Digits, place values, and the Arabic form with separators.
    n constant text := '[0-9,〇零一二兩三四五六七八九壹貳參肆伍陸柒捌玖十拾百佰千仟萬億]+';
    m text[];
BEGIN
    IF amount_text IS NULL THEN
        RETURN NULL;
    END IF;

    m := regexp_match(amount_text, '(' || n || ')元以上(' || n || ')元以下');
    IF m IS NOT NULL THEN
        RETURN ARRAY[chinese_numeral(m[1]), chinese_numeral(m[2])];
    END IF;

    m := regexp_match(amount_text, '(' || n || ')元以下');
    IF m IS NOT NULL THEN
        RETURN ARRAY[NULL, chinese_numeral(m[1])]::numeric[];
    END IF;

    m := regexp_match(amount_text, '(' || n || ')元以上');
    IF m IS NOT NULL THEN
        RETURN ARRAY[chinese_numeral(m[1]), NULL]::numeric[];
    END IF;

    RETURN NULL;
END;
$$;

CREATE FUNCTION read_statutory_fine_min(amount_text text) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    RETURN (fine_bounds(amount_text))[1];

CREATE FUNCTION read_statutory_fine_max(amount_text text) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    RETURN (fine_bounds(amount_text))[2];

-- ---------------------------------------------------------------------------
-- Penalties
-- ---------------------------------------------------------------------------

CREATE TYPE legal_effect AS ENUM (
    'fine',              -- 罰鍰
    'order_to_remedy',   -- 限期改善、限期補正
    'repeated_penalty',  -- 按次處罰、按日連續處罰
    'suspension',        -- 停工、停業
    'revocation',        -- 撤銷、廢止許可證照
    'name_disclosure',   -- 公布姓名或名稱
    'criminal_referral'  -- 移送司法機關
);

CREATE TABLE sanction (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- The article imposing the penalty.
    sanction_iri text NOT NULL REFERENCES subdivision (iri) ON DELETE CASCADE,

    -- The duty whose breach it punishes. NULL is a real case, not a gap: an
    -- article may read 「違反本法規定者」 without naming a provision.
    --
    -- Points at an article, because that is the granularity the corpus holds.
    -- Statutes often punish a specific paragraph (「違反第二十條第一項」); once
    -- paragraphs carry text this should narrow, and until then the reference is
    -- to the article that contains it — coarser than the statute, never wrong.
    violated_iri text REFERENCES subdivision (iri) ON DELETE CASCADE,

    effect legal_effect NOT NULL,

    -- The clause stating the amount, verbatim from the article. This is the
    -- truth; everything numeric below is read out of it.
    amount_text text CHECK (amount_text <> ''),

    -- 法定罰鍰最低額／最高額 (行政罰法 §18). NOT what will be imposed: §18 II
    -- permits exceeding the maximum where the gain did, and §18 III permits going
    -- below the minimum on mitigation. Both bounds are inclusive (刑法 §10 I).
    statutory_fine_min numeric GENERATED ALWAYS AS (read_statutory_fine_min(amount_text)) STORED,
    statutory_fine_max numeric GENERATED ALWAYS AS (read_statutory_fine_max(amount_text)) STORED,

    -- One article commonly carries several effects (「處罰鍰，並命限期改善」).
    -- Each is its own row so that "how much" and "what else" are both plain
    -- filters; the pair below keeps the same effect from being stated twice.
    CONSTRAINT sanction_unique
        UNIQUE NULLS NOT DISTINCT (sanction_iri, violated_iri, effect),

    -- An amount belongs to a monetary effect. A suspension with a sum attached
    -- is a data error, and one that stores cleanly is one nobody will find.
    CONSTRAINT sanction_amount_only_when_monetary
        CHECK (amount_text IS NULL OR effect IN ('fine', 'repeated_penalty')),

    -- A fine says how much. Without that it is an assertion with no article
    -- behind it.
    CONSTRAINT sanction_fine_states_an_amount
        CHECK (effect <> 'fine' OR amount_text IS NOT NULL),

    CONSTRAINT sanction_bounds_ordered
        CHECK (statutory_fine_min IS NULL
               OR statutory_fine_max IS NULL
               OR statutory_fine_min <= statutory_fine_max)
);

CREATE INDEX sanction_by_article ON sanction (sanction_iri);
CREATE INDEX sanction_by_violated ON sanction (violated_iri);
-- Range queries over the statutory maximum: 「罰鍰一百萬元以上的條文有哪些」.
CREATE INDEX sanction_by_amount ON sanction (statutory_fine_max, statutory_fine_min)
    WHERE effect = 'fine';

-- ---------------------------------------------------------------------------
-- Gaps, enumerable
-- ---------------------------------------------------------------------------

-- A fine whose amount could not be read. The row is kept — the article does
-- impose a fine — but it is listed here rather than silently carrying NULLs that
-- look like 「條文未定上限」.
CREATE VIEW sanction_amount_unreadable AS
SELECT s.id, s.sanction_iri, s.amount_text
  FROM sanction AS s
 WHERE s.effect = 'fine'
   AND s.amount_text IS NOT NULL
   AND s.statutory_fine_min IS NULL
   AND s.statutory_fine_max IS NULL;

-- Penalties pointing at no duty. Some are genuine (「違反本法規定者」); the rest
-- are extraction that has not happened yet. Which is which needs a human, so the
-- view lists both rather than pretending to know.
CREATE VIEW sanction_without_duty AS
SELECT s.id, s.sanction_iri, s.effect, d.resource_id, d.number
  FROM sanction AS s
  JOIN subdivision AS d ON d.iri = s.sanction_iri
 WHERE s.violated_iri IS NULL;

-- A penalty must rest on a duty. Where one points at a definition or a
-- procedural provision, that is a modelling error, and 0006's may_ground_sanction
-- is what says so. Listed, not refused: the role assignment may simply be wrong,
-- and refusing the insert would hide the disagreement instead of showing it.
CREATE VIEW sanction_on_ungroundable_norm AS
SELECT s.id, s.sanction_iri, s.violated_iri, r.role
  FROM sanction AS s
  JOIN subdivision_role AS r ON r.subdivision_iri = s.violated_iri
 WHERE s.violated_iri IS NOT NULL
   AND NOT EXISTS (
        SELECT 1
          FROM subdivision_role AS ok
         WHERE ok.subdivision_iri = s.violated_iri
           AND may_ground_sanction(ok.role)
   );
