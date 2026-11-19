-- 0011_annex_provision — 附表單元的路徑、類型與排序
--
-- 附表是法規底下的單元，路徑 `annex_<號>`（`annex_3`；附表十二之一是 `annex_12-1`），和條一樣是根：
-- 沒有上層，也沒有項款目。它排在所有條之後，附表之間依號數。
--
-- 既有條文單元的路徑、類型與排序鍵一律不變：只放寬路徑形狀與根的條件，並讓 `path_type`、
-- `path_sort_key` 認得 `annex_`。

CREATE OR REPLACE FUNCTION corpus.path_type(p text) RETURNS corpus.provision_type
    LANGUAGE sql IMMUTABLE
    AS $_$
    SELECT CASE
        WHEN p ~ '^annex_' THEN 'annex'
        WHEN p ~ '/sub_[0-9]+$' THEN 'subitem'
        WHEN p ~ '/item_[0-9]+$' THEN 'item'
        WHEN p ~ '/pt_[0-9]+$' THEN 'subparagraph'
        WHEN p ~ '/para_[0-9]+$' THEN 'paragraph'
        ELSE 'article'
    END::corpus.provision_type
$_$;

CREATE OR REPLACE FUNCTION corpus.path_sort_key(p text) RETURNS integer[]
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    head text := split_part(p, '/', 1);
    key integer[];
    seg text;
BEGIN
    IF head LIKE 'annex\_%' THEN
        -- 排在所有條之後：第一個數字比任何條號都大。
        RETURN ARRAY[1000000] || ARRAY(
            SELECT x::integer FROM regexp_split_to_table(substring(head FROM 7), '-') AS x
        );
    END IF;
    key := ARRAY(
        SELECT x::integer FROM regexp_split_to_table(substring(head FROM 5), '-') AS x
    );
    IF p = head THEN
        RETURN key;
    END IF;
    FOREACH seg IN ARRAY string_to_array(substring(p FROM length(head) + 2), '/') LOOP
        key := key || ARRAY[0, split_part(seg, '_', 2)::integer];
    END LOOP;
    RETURN key;
END
$$;

ALTER TABLE corpus.provision
    DROP CONSTRAINT provision_only_article_is_root,
    DROP CONSTRAINT provision_path_shape;

ALTER TABLE corpus.provision
    ADD CONSTRAINT provision_only_article_is_root CHECK (
        (parent_id IS NULL) = (provision_type IN ('article', 'annex'))
    ),
    ADD CONSTRAINT provision_path_shape CHECK (
        path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+(/pt_[0-9]+(/item_[0-9]+(/sub_[0-9]+)?)?)?)?$'
        OR path ~ '^annex_[0-9]+(-[0-9]+)*$'
    );
