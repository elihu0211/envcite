-- 0065_structure_heading — 條之上的結構章節（編、章、節、款、目）
--
-- 結構章節隨公布版本（legal_expression）而異，不屬於跨版本不變的 corpus.provision，
-- 故另建輕量實表，並與 corpus.provision.sort_key 共用 integer[] 空間：
-- 單一 ORDER BY sort_key 即可把「編 → 章 → 節 → 款 → 目 → 第 N 條」依文件順序交錯排列，
-- 現有的陳述與授權錨定仍只指向 article 以下的 provision，完全不受影響。

CREATE TYPE corpus.heading_kind AS ENUM (
    'part',          -- 編
    'chapter',       -- 章
    'section',       -- 節
    'subsection',    -- 款
    'item_heading'   -- 目
);

CREATE TABLE corpus.structure_heading (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resource_id    bigint NOT NULL REFERENCES corpus.legal_resource ON DELETE CASCADE,
    expression_id  bigint NOT NULL REFERENCES corpus.legal_expression ON DELETE CASCADE,
    parent_id      bigint,
    heading_kind   corpus.heading_kind NOT NULL,
    heading_no     text NOT NULL CHECK (heading_no <> ''),
    title          text NOT NULL,
    sort_key       integer[] NOT NULL CHECK (cardinality(sort_key) > 0),
    UNIQUE (id, expression_id),
    FOREIGN KEY (parent_id, expression_id)
        REFERENCES corpus.structure_heading (id, expression_id) ON DELETE CASCADE
);

COMMENT ON TABLE corpus.structure_heading IS
    '條之上的結構章節（編、章、節、款、目），依附於特定公布版本並以 sort_key 與條文交錯排序。';
COMMENT ON COLUMN corpus.structure_heading.sort_key IS
    '與 corpus.provision.sort_key 共用的整數陣列排序鍵；置於所屬第一條之前。';

CREATE INDEX structure_heading_by_expression
    ON corpus.structure_heading (expression_id, sort_key);
