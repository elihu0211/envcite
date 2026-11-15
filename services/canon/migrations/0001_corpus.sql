-- 0001_corpus — 法規語料：法規、版本、條文單元、生命週期事件
--
-- 設計見 envcite-docs 02-04 §5 與 03-02。本檔只建立現在有生產者與使用者的部分；
-- 標註、規範關係、受控詞彙、取得紀錄等，隨其管線落地時各以新遷移加入。

-- 時態鍵（WITHOUT OVERLAPS）以 GiST 實作，bigint 的等值比較需要 btree_gist。
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA corpus;

-- 識別碼方案：法規的自然鍵是（方案，在地識別碼），不是單一字串。
CREATE TYPE corpus.id_scheme AS ENUM (
    'moj_pcode'      -- 全國法規資料庫 pcode
);

-- 規範種類與位階（01-01 §2）。行政規則不對外發生法規範效力，另立一軸。
CREATE TYPE corpus.norm_kind AS ENUM (
    'constitution', 'statute', 'regulation',
    'local_ordinance', 'local_regulation', 'administrative_rule'
);

CREATE TYPE corpus.text_status AS ENUM ('available', 'unavailable');

-- 文字地位：兩個來源不一致時以何者為準（03-02 §6）。
CREATE TYPE corpus.text_authority AS ENUM (
    'gazette', 'competent_authority', 'official_db', 'unverified'
);

-- 中央法規標準法第 8 條：條、項、款、目、目之 1。
CREATE TYPE corpus.provision_type AS ENUM (
    'article', 'paragraph', 'subparagraph', 'item', 'subitem'
);

CREATE TYPE corpus.event_kind AS ENUM (
    'enactment', 'full_amendment', 'partial_amendment',
    'commencement', 'repeal', 'sunset', 'suspension', 'unconstitutional', 'correction'
);

CREATE TABLE corpus.legal_resource (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    id_scheme     corpus.id_scheme NOT NULL,
    local_id      text NOT NULL,
    kind          corpus.norm_kind NOT NULL,
    -- 來源自己的分類（編制表、組織規程……），與位階分立。
    document_type text NOT NULL CHECK (document_type <> ''),
    UNIQUE (id_scheme, local_id),
    CONSTRAINT legal_resource_pcode_shape
        CHECK (id_scheme <> 'moj_pcode' OR local_id ~ '^[A-Z][0-9]{7}$')
);

-- 版本：每一次公布或發布所生的文字。標題屬表達層（eli:title 的定義域）。
CREATE TABLE corpus.legal_expression (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id  bigint NOT NULL REFERENCES corpus.legal_resource ON DELETE CASCADE,
    language     text NOT NULL CHECK (language ~ '^[a-z]{3}$'),   -- ISO 639-3，與 IRI 同形
    version_date date NOT NULL,
    title        text NOT NULL CHECK (title <> ''),
    -- 文字時間：自本次公布起，至下次公布止。
    text_period  daterange NOT NULL CHECK (NOT isempty(text_period) AND lower(text_period) = version_date),
    text_status  corpus.text_status NOT NULL,
    authority    corpus.text_authority NOT NULL,
    UNIQUE (resource_id, language, version_date),
    UNIQUE (id, resource_id),
    UNIQUE (resource_id, language, text_period WITHOUT OVERLAPS)
);

-- 條文單元的位置，跨版本不變：「第 20 條第 2 項」是同一個 provision。
CREATE TABLE corpus.provision (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id    bigint NOT NULL REFERENCES corpus.legal_resource ON DELETE CASCADE,
    parent_id      bigint,
    provision_type corpus.provision_type NOT NULL,
    path           text NOT NULL
        CHECK (path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+(/pt_[0-9]+(/item_[0-9]+(/sub_[0-9]+)?)?)?)?$'),
    -- 閱讀順序：「5-1」在「5」之後、「6」之前，是整數陣列而非字串的順序。下層單元
    -- 為上層的鍵加 0 再加序位（第 2 條第 1 項為 {2,0,1}），使其排在「2-1」之前。
    sort_key       integer[] NOT NULL,
    UNIQUE (resource_id, path),
    UNIQUE (id, resource_id),
    -- 上層單元屬於同一部法規。
    FOREIGN KEY (parent_id, resource_id) REFERENCES corpus.provision (id, resource_id) ON DELETE CASCADE,
    CONSTRAINT provision_only_articles_are_roots CHECK ((parent_id IS NULL) = (provision_type = 'article'))
);

-- 條號：由路徑導出，不另存一份會分岔的副本。
CREATE FUNCTION corpus.article_number(path text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    RETURN substring(path FROM '^art_([0-9]+(?:-[0-9]+)*)');

-- 某位置在某版本中的文字與效力。
CREATE TABLE corpus.provision_version (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provision_id   bigint NOT NULL,
    expression_id  bigint NOT NULL,
    resource_id    bigint NOT NULL,
    -- 文字存於最細的單元；有下層的單元為空值，其全文由 article_text 組成。
    content        text CHECK (content <> ''),
    -- 效力區間，逐單元記載；尚未施行者為空值，不參與時態鍵的比較。
    in_force       daterange CHECK (NOT isempty(in_force)),
    UNIQUE (provision_id, expression_id),
    UNIQUE (provision_id, in_force WITHOUT OVERLAPS),
    -- 單元與版本屬於同一部法規。
    FOREIGN KEY (provision_id, resource_id) REFERENCES corpus.provision (id, resource_id) ON DELETE CASCADE,
    FOREIGN KEY (expression_id, resource_id) REFERENCES corpus.legal_expression (id, resource_id) ON DELETE CASCADE
);

CREATE INDEX provision_version_by_expression ON corpus.provision_version (expression_id);

CREATE TABLE corpus.lifecycle_event (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id   bigint NOT NULL REFERENCES corpus.legal_resource ON DELETE CASCADE,
    kind          corpus.event_kind NOT NULL,
    event_date    date NOT NULL,
    document_no   text CHECK (document_no <> ''),
    -- 公布或發布所生的版本；其他種類的事件不生版本。
    expression_id bigint,
    FOREIGN KEY (expression_id, resource_id) REFERENCES corpus.legal_expression (id, resource_id) ON DELETE CASCADE,
    CONSTRAINT lifecycle_event_expression_only_for_promulgation
        CHECK (expression_id IS NULL OR kind IN ('enactment', 'full_amendment', 'partial_amendment'))
);

CREATE INDEX lifecycle_event_by_resource ON corpus.lifecycle_event (resource_id, kind);

-- IRI 由自然鍵導出，不存：樣板見 03-02 §3。
CREATE FUNCTION corpus.resource_iri(kind corpus.norm_kind, local_id text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    RETURN 'https://envcite/eli/tw/'
        || CASE WHEN kind IN ('constitution', 'statute') THEN 'law' ELSE 'order' END
        || '/' || local_id;

-- 條文單元版本的 IRI：法規 IRI／版本日／語言／路徑。
CREATE FUNCTION corpus.version_iri(p_version bigint) RETURNS text
    LANGUAGE sql STABLE STRICT
    RETURN (
        SELECT corpus.resource_iri(r.kind, r.local_id) || '/' || e.version_date || '/' || e.language || '/' || p.path
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
          JOIN corpus.legal_resource AS r ON r.id = v.resource_id
         WHERE v.id = p_version
    );

-- 條的全文：無下層者即其內容；有下層者依閱讀順序組成最細單元的內容。
CREATE FUNCTION corpus.article_text(p_version bigint) RETURNS text
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
                AND cv.content IS NOT NULL)
        )
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id
         WHERE v.id = p_version
    );

CREATE TYPE corpus.provision_status AS ENUM (
    'in_force', 'text_unavailable', 'not_yet_in_force', 'no_longer_in_force'
);

-- 某日的法規，逐單元解析：每一單元取效力區間涵蓋該日的版本（03-02 §5）。
-- 該日尚未公布的單元不列出；不以其他版本的文字頂替無法取得的文字。
CREATE FUNCTION corpus.provisions_at(p_resource bigint, p_language text, p_on date)
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
               WHEN EXISTS (
                   SELECT 1
                     FROM corpus.provision_version AS v
                     JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                    WHERE v.provision_id = p.id
                      AND e.language = p_language
                      AND e.version_date <= p_on
                      AND (v.in_force IS NULL OR lower(v.in_force) > p_on)
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
              AND v.in_force @> p_on
      ) AS cur ON true
     WHERE p.resource_id = p_resource
       AND EXISTS (
           SELECT 1
             FROM corpus.provision_version AS v
             JOIN corpus.legal_expression AS e ON e.id = v.expression_id
            WHERE v.provision_id = p.id
              AND e.language = p_language
              AND e.version_date <= p_on
       )
     ORDER BY p.sort_key, p.path;
$$;
