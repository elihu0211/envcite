-- 0043_constitutional_ruling — 違憲失效：大法官解釋與憲法法庭判決所宣告失效的條文
--
-- 不寫進 lifecycle_event：匯入器重寫一部法規時會刪光它的事件（ingest/write.py），而失效事件
-- 的來源是憲法法庭、不是全國法規資料庫，不能隨法規重匯而消失。
--
-- - 一則裁判一列，一則裁判可使數個條文單元失效。裁判由人載入並附解釋文原文，不由程式
--   從文字推斷失效日：失效日在解釋文裡是自然語言（「至遲於屆滿一年時失其效力」）。
-- - 失效不固定目標版本，依查詢日解析（invalidations_at）：只有裁判作成時已在施行的文字
--   被宣告失效；裁判之後修正的新文字不受影響。
-- - 停止適用（自公布日起）同樣記為 invalid_from = 公布日。

CREATE TYPE corpus.ruling_kind AS ENUM ('interpretation', 'judgment');

CREATE TABLE corpus.constitutional_ruling (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ruling_no   text NOT NULL UNIQUE CHECK (ruling_no = btrim(ruling_no) AND ruling_no <> ''),
    ruling_kind corpus.ruling_kind NOT NULL,
    decided_on  date NOT NULL,
    source_url  text NOT NULL CHECK (source_url ~ '^https://'),
    -- 解釋文（判決主文）中宣告失效的原文，供人核對失效日。
    excerpt     text NOT NULL CHECK (excerpt <> '')
);

CREATE TABLE corpus.ruling_invalidation (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ruling_id    bigint NOT NULL REFERENCES corpus.constitutional_ruling ON DELETE CASCADE,
    resource_id  bigint NOT NULL,
    provision_id bigint NOT NULL,
    invalid_from date NOT NULL,
    UNIQUE (ruling_id, provision_id),
    FOREIGN KEY (provision_id, resource_id)
        REFERENCES corpus.provision (id, resource_id) ON DELETE CASCADE
);

CREATE INDEX ruling_invalidation_by_provision ON corpus.ruling_invalidation (provision_id);

-- invalid_from 不早於裁判日（裁判不溯及既往地宣告失效）。
CREATE FUNCTION corpus.ruling_invalidation_dated() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.invalid_from < (SELECT decided_on FROM corpus.constitutional_ruling WHERE id = NEW.ruling_id) THEN
        RAISE EXCEPTION 'invalid_from % precedes the ruling date', NEW.invalid_from;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ruling_invalidation_dated
    BEFORE INSERT OR UPDATE ON corpus.ruling_invalidation
    FOR EACH ROW EXECUTE FUNCTION corpus.ruling_invalidation_dated();

-- 某部法規在某日因違憲失效而不再有效的條文單元。
-- version_id 是該日現行、且在裁判日已施行的版本（即被宣告失效的文字）。
CREATE FUNCTION corpus.invalidations_at(p_resource bigint, p_language text, p_on date)
RETURNS TABLE(
    ruling_id    bigint,
    ruling_no    text,
    provision_id bigint,
    path         text,
    version_id   bigint,
    invalid_from date
)
LANGUAGE sql STABLE
AS $$
    SELECT r.id, r.ruling_no, i.provision_id, p.path, a.version_id, i.invalid_from
      FROM corpus.ruling_invalidation AS i
      JOIN corpus.constitutional_ruling AS r ON r.id = i.ruling_id
      JOIN corpus.provision AS p ON p.id = i.provision_id
      JOIN LATERAL corpus.provisions_at(i.resource_id, p_language, p_on) AS a
           ON a.provision_id = i.provision_id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
     WHERE i.resource_id = p_resource
       AND i.invalid_from <= p_on
       AND lower(v.force_period) <= r.decided_on
     ORDER BY p.sort_key, r.decided_on, r.id
$$;
