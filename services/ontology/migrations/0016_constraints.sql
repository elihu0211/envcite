-- 0016_constraints — 把設計裡已決定、資料已經符合的約束交給資料庫
--
-- 見 env-law-docs 02-05 F1、F5、F6、F7 與 02-04 的 corpus 約束表。
-- 排序鍵與所屬條路徑改由路徑生成；語言從版本抄來，排除約束才分得開語言；
-- 廢止日是效力上界；連到版本的事件，事件日必須就是那個版本日。

CREATE FUNCTION corpus.path_sort_key(p text) RETURNS integer[]
LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE
    head text := split_part(p, '/', 1);
    key integer[] := ARRAY(
        SELECT x::integer FROM regexp_split_to_table(substring(head FROM 5), '-') AS x
    );
    seg text;
BEGIN
    IF p = head THEN
        RETURN key;
    END IF;
    FOREACH seg IN ARRAY string_to_array(substring(p FROM length(head) + 2), '/') LOOP
        key := key || ARRAY[0, split_part(seg, '_', 2)::integer];
    END LOOP;
    RETURN key;
END
$fn$;

CREATE FUNCTION corpus.path_type(p text) RETURNS corpus.provision_type
LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE
        WHEN p ~ '/sub_[0-9]+$' THEN 'subitem'
        WHEN p ~ '/item_[0-9]+$' THEN 'item'
        WHEN p ~ '/pt_[0-9]+$' THEN 'subparagraph'
        WHEN p ~ '/para_[0-9]+$' THEN 'paragraph'
        ELSE 'article'
    END::corpus.provision_type
$fn$;

DROP FUNCTION corpus.article_text(bigint);
ALTER TABLE corpus.provision DROP COLUMN sort_key;
ALTER TABLE corpus.provision
    ADD COLUMN sort_key integer[] GENERATED ALWAYS AS (corpus.path_sort_key(path)) STORED,
    ADD COLUMN article_path text GENERATED ALWAYS AS (split_part(path, '/', 1)) STORED;

ALTER TABLE corpus.provision
    ADD CONSTRAINT provision_path_shape CHECK (
        path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+(/pt_[0-9]+(/item_[0-9]+(/sub_[0-9]+)?)?)?)?$'
    ),
    ADD CONSTRAINT provision_type_matches_path CHECK (provision_type = corpus.path_type(path)),
    ADD CONSTRAINT provision_only_article_is_root CHECK ((parent_id IS NULL) = (provision_type = 'article'));

CREATE FUNCTION corpus.provision_parent_path() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    parent_path text;
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT path INTO parent_path FROM corpus.provision WHERE id = NEW.parent_id;
    IF parent_path IS DISTINCT FROM regexp_replace(NEW.path, '/[^/]+$', '') THEN
        RAISE EXCEPTION 'parent path % does not prefix %', parent_path, NEW.path
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER provision_parent_path
    BEFORE INSERT OR UPDATE OF path, parent_id ON corpus.provision
    FOR EACH ROW EXECUTE FUNCTION corpus.provision_parent_path();

ALTER TABLE corpus.provision_version ADD COLUMN language text;

UPDATE corpus.provision_version AS v
   SET language = e.language
  FROM corpus.legal_expression AS e
 WHERE e.id = v.expression_id;

ALTER TABLE corpus.provision_version ALTER COLUMN language SET NOT NULL;

CREATE FUNCTION corpus.copy_version_language() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    SELECT e.language INTO NEW.language
      FROM corpus.legal_expression AS e
     WHERE e.id = NEW.expression_id;
    IF NEW.language IS NULL THEN
        RAISE EXCEPTION 'expression % has no language', NEW.expression_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER provision_version_language
    BEFORE INSERT OR UPDATE OF expression_id ON corpus.provision_version
    FOR EACH ROW EXECUTE FUNCTION corpus.copy_version_language();

ALTER TABLE corpus.legal_expression
    ADD CONSTRAINT legal_expression_id_resource_language UNIQUE (id, resource_id, language),
    ADD CONSTRAINT legal_expression_id_resource_version_date UNIQUE (id, resource_id, version_date);

ALTER TABLE corpus.provision_version
    ADD CONSTRAINT provision_version_expression_language
        FOREIGN KEY (expression_id, resource_id, language)
        REFERENCES corpus.legal_expression (id, resource_id, language)
        ON DELETE CASCADE;

ALTER TABLE corpus.provision_version DROP CONSTRAINT provision_version_current_no_overlap;
ALTER TABLE corpus.provision_version
    ADD CONSTRAINT provision_version_current_no_overlap
        EXCLUDE USING gist (provision_id WITH =, language WITH =, force_period WITH &&)
        WHERE (superseded_at IS NULL);

ALTER TABLE corpus.provision_version
    ADD CONSTRAINT provision_version_force_follows_rule CHECK (
        (force_period IS NULL) = (commence_rule IN ('by_order', 'unconfirmed'))
    );

CREATE FUNCTION corpus.force_within_repeal() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    repeal date;
BEGIN
    IF NEW.superseded_at IS NOT NULL OR NEW.force_period IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT repealed_on INTO repeal FROM corpus.legal_resource WHERE id = NEW.resource_id;
    IF repeal IS NOT NULL AND (upper(NEW.force_period) IS NULL OR upper(NEW.force_period) > repeal) THEN
        RAISE EXCEPTION 'force period extends past repeal on %', repeal
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER provision_version_within_repeal
    BEFORE INSERT OR UPDATE OF force_period, superseded_at, resource_id
    ON corpus.provision_version
    FOR EACH ROW EXECUTE FUNCTION corpus.force_within_repeal();

CREATE FUNCTION corpus.repeal_caps_force() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.repealed_on IS NOT NULL AND EXISTS (
        SELECT 1 FROM corpus.provision_version AS v
         WHERE v.resource_id = NEW.id
           AND v.superseded_at IS NULL
           AND v.force_period IS NOT NULL
           AND (upper(v.force_period) IS NULL OR upper(v.force_period) > NEW.repealed_on)
    ) THEN
        RAISE EXCEPTION 'repeal on % leaves a current version in force', NEW.repealed_on
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

CREATE TRIGGER legal_resource_repeal_caps_force
    BEFORE UPDATE OF repealed_on ON corpus.legal_resource
    FOR EACH ROW EXECUTE FUNCTION corpus.repeal_caps_force();

ALTER TABLE corpus.lifecycle_event
    ADD CONSTRAINT lifecycle_event_expression_is_promulgation CHECK (
        expression_id IS NULL
        OR (event_date IS NOT NULL AND event_kind IN ('enactment', 'full_amendment', 'partial_amendment'))
    ),
    ADD CONSTRAINT lifecycle_event_matches_expression
        FOREIGN KEY (expression_id, resource_id, event_date)
        REFERENCES corpus.legal_expression (id, resource_id, version_date)
        ON DELETE CASCADE;


CREATE OR REPLACE FUNCTION corpus.article_text(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (
        SELECT coalesce(
            v.content,
            (SELECT string_agg(cv.content, E'\n' ORDER BY c.sort_key)
               FROM corpus.provision AS c
               JOIN corpus.provision_version AS cv
                 ON cv.provision_id = c.id AND cv.expression_id = v.expression_id
              WHERE c.resource_id = p.resource_id
                AND c.path LIKE p.path || '/%'
                AND cv.superseded_at IS NULL
                AND cv.content IS NOT NULL)
        )
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id
         WHERE v.id = p_version
    );

CREATE OR REPLACE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE (
    provision_id   bigint,
    path           text,
    provision_type corpus.provision_type,
    sort_key       integer[],
    version_id     bigint,
    status         corpus.provision_status
)
LANGUAGE sql STABLE
AS $$
    SELECT p.id, p.path, p.provision_type, p.sort_key, cur.id,
           CASE
               WHEN cur.id IS NOT NULL AND cur.text_status = 'unavailable' THEN 'text_unavailable'
               WHEN cur.id IS NOT NULL THEN 'in_force'
               WHEN (SELECT repealed_on FROM corpus.legal_resource WHERE id = p_resource) <= p_on
                   THEN 'no_longer_in_force'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.superseded_at IS NULL
                      AND v.commence_rule = 'unconfirmed'
               ) THEN 'commencement_unconfirmed'
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND v.superseded_at IS NULL
                      AND (v.force_period IS NULL OR lower(v.force_period) > p_on)
               ) THEN 'not_yet_in_force'
               ELSE 'no_longer_in_force'
           END::corpus.provision_status
      FROM corpus.provision AS p
      LEFT JOIN LATERAL (
           SELECT v.id, e.text_status
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND v.superseded_at IS NULL
              AND v.force_period @> p_on
      ) AS cur ON true
     WHERE p.resource_id = p_resource
       AND EXISTS (
           SELECT 1
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND e.version_date <= p_on
              AND v.superseded_at IS NULL
       )
     ORDER BY p.sort_key, p.path;
$$;
