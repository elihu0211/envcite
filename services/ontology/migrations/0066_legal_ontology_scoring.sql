-- 0066_legal_ontology_scoring — 法學與環境法本體擴充：領域分面、量綱單位、跨法概念關係、準用、多維限值解析、與環境裁罰／記點／講習矩陣
--
-- 本遷移一次補齊專論 01-01 至 01-09 所缺的四個本體骨幹：
--   1. vocab 領域分面（domain_facet）、標準量綱單位（canonical_unit）與跨法概念關係（concept_relation），
--      使受控詞彙承載環境法六元組語義（行為人、設施、污染物、介質、管制區、量測、許可、程序節點、裁量因子）
--      並支援基於法定定義與外部標準（CAS/SIC）的受控跨法涵攝與單位換算檢查。
--   2. annotation.priority_basis 擴充法條競合型態（subsidiarity 補充關係、absorption 吸收關係、
--      mutatis_mutandis 準用），並新增 annotation.mutatis_mutandis 記載準用之槽位／概念對映。
--   3. annotation.threshold_sources_at 回傳所有符合條文與概念之已確認限值陳述，交由推論機依各限值的
--      scope 條件（行業別、管制區、污染源時期、製程規模）動態篩選，消除「取 min(condition_id) 任選一筆」之結構缺陷。
--   4. scoring 結構綱要：以法條內容雜湊雙重錨定台灣環境法三大裁罰計分模型
--      （水污法點數基數制、空污/廢清/毒管倍數相乘制、營建工程缺失記點累積制）及環境教育法環境講習時數矩陣，
--      區分法規命令（裁罰準則）與行政規則（裁罰基準／處理原則），維持「矩陣是資料、引擎不寫死法條」不變式。

-- 一、環境與法學領域本體分面、量綱單位與受控跨法概念關係
CREATE TYPE vocab.concept_domain_facet AS ENUM (
    'general',
    'regulated_actor',
    'facility_source',
    'pollutant_substance',
    'environmental_medium',
    'control_zone',
    'measurement_spec',
    'permit_instrument',
    'procedural_milestone',
    'legal_status_def',
    'discretion_factor'
);

COMMENT ON TYPE vocab.concept_domain_facet IS
    '受控詞彙的環境法與法學領域本體分面（對齊 LKIF-Core 與環境管制六元組）。';

ALTER TABLE vocab.concept
    ADD COLUMN IF NOT EXISTS domain_facet vocab.concept_domain_facet NOT NULL DEFAULT 'general',
    ADD COLUMN IF NOT EXISTS canonical_unit text;

COMMENT ON COLUMN vocab.concept.domain_facet IS
    '概念所屬之環境法本體分面；未分類預設為 general。';
COMMENT ON COLUMN vocab.concept.canonical_unit IS
    '數值概念之標準量綱單位（如 mg/L、ug/L、ppm、mg/Nm3、dB(A)、m3/day、NTD、point、hour）。';

CREATE TYPE vocab.concept_relation_kind AS ENUM ('broader', 'exact_match');

CREATE TABLE vocab.concept_relation (
    source_concept_id  bigint NOT NULL REFERENCES vocab.concept (id) ON DELETE CASCADE,
    target_concept_id  bigint NOT NULL REFERENCES vocab.concept (id) ON DELETE CASCADE,
    relation_kind      vocab.concept_relation_kind NOT NULL,
    basis_provision_id bigint REFERENCES corpus.provision (id) ON DELETE SET NULL,
    PRIMARY KEY (source_concept_id, target_concept_id, relation_kind),
    CHECK (source_concept_id <> target_concept_id)
);

CREATE INDEX concept_relation_by_target ON vocab.concept_relation (target_concept_id, relation_kind);

COMMENT ON TABLE vocab.concept_relation IS
    '受控詞彙之間的法定上位（broader）或等同（exact_match）關係；跨法涵攝僅得依此表或 concept_match 成立，不得無據跨法混用。';

-- 二、規範優先與準用關係擴充（中標法 §17、法條競合）
ALTER TYPE annotation.priority_basis ADD VALUE IF NOT EXISTS 'mutatis_mutandis';
ALTER TYPE annotation.priority_basis ADD VALUE IF NOT EXISTS 'subsidiarity';
ALTER TYPE annotation.priority_basis ADD VALUE IF NOT EXISTS 'absorption';

CREATE TABLE annotation.mutatis_mutandis (
    id                  bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    source_statement_id bigint NOT NULL REFERENCES annotation.statement (id) ON DELETE CASCADE,
    target_statement_id bigint NOT NULL REFERENCES annotation.statement (id) ON DELETE CASCADE,
    mapping_notes       text NOT NULL DEFAULT '',
    slot_adaptations    jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_statement_id, target_statement_id),
    CHECK (source_statement_id <> target_statement_id)
);

CREATE INDEX mutatis_mutandis_by_source ON annotation.mutatis_mutandis (source_statement_id);

COMMENT ON TABLE annotation.mutatis_mutandis IS
    '準用關係（中央法規標準法第 17 條）：source_statement 準用 target_statement，並以 slot_adaptations 記載概念或主體替換對映。';

-- 三、多維適用條件之授權限值集合查詢（保留 threshold_source_at 向下相容，新增 threshold_sources_at）
CREATE FUNCTION annotation.threshold_sources_at(p_condition bigint, p_language text, p_on date)
 RETURNS TABLE(threshold numeric, statement_id bigint, condition_id bigint,
               measurement_method text, averaging_period text)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT lim.threshold, ls.id, lim.id, mm.code, ap.code
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
      JOIN LATERAL corpus.provisions_at(dp.resource_id, p_language, p_on) AS a
           ON a.provision_id = dp.id AND a.status = 'in_force'
      JOIN corpus.provision_version AS v ON v.id = a.version_id
      JOIN annotation.statement AS ls ON ls.provision_id = v.provision_id
                                     AND ls.content_sha256 = v.content_sha256
      JOIN annotation.assertion AS lc ON lc.id = ls.id AND lc.confirm_state = 'confirmed'
      JOIN annotation.statement_condition AS lim ON lim.statement_id = ls.id
                                                AND lim.condition_role = 'limit'
                                                AND lim.concept_id = c.concept_id
      LEFT JOIN vocab.concept AS mm ON mm.id = lim.measurement_method_concept_id
      LEFT JOIN vocab.concept AS ap ON ap.id = lim.averaging_period_concept_id
     WHERE c.id = p_condition
       AND lim.threshold IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
             FROM corpus.invalidations_at(dp.resource_id, p_language, p_on) AS i
            WHERE i.version_id = v.id
       )
     ORDER BY ls.id, lim.id
$function$;

COMMENT ON FUNCTION annotation.threshold_sources_at(bigint, text, date) IS
    '依查詢日解析委由授權條文的所有已確認限值陳述（含檢測方法與平均時段）；供推論機依各限值之 scope 條件動態擇定適用標準。';

-- 四、環境裁罰矩陣、缺失記點與環境講習結構綱要（scoring）
CREATE SCHEMA scoring;

CREATE TYPE scoring.formula_kind AS ENUM (
    'additive_points_times_base',
    'multiplicative_factors',
    'demerit_accumulation',
    'fixed_schedule'
);

CREATE TYPE scoring.factor_role AS ENUM (
    'base_point',
    'aggravation_point',
    'mitigation_point',
    'multiplicative_factor',
    'additive_rate',
    'demerit_point',
    'fixed_amount'
);

CREATE TABLE scoring.penalty_matrix (
    id                bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    provision_id      bigint NOT NULL REFERENCES corpus.provision (id) ON DELETE CASCADE,
    content_sha256    bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    matrix_code       text NOT NULL UNIQUE CHECK (matrix_code ~ '^[a-z0-9_]+$'),
    law_title         text NOT NULL,
    norm_kind         text NOT NULL CHECK (norm_kind IN ('delegated_regulation', 'discretion_standard')),
    formula_kind      scoring.formula_kind NOT NULL,
    legal_effect      annotation.legal_effect NOT NULL DEFAULT 'fine',
    base_amount       numeric(18, 3),
    statutory_min     numeric(18, 3),
    statutory_max     numeric(18, 3),
    demerit_threshold numeric(18, 3),
    rounding_mode     text NOT NULL DEFAULT 'floor_ntd'
        CHECK (rounding_mode IN ('floor_ntd', 'round_ntd', 'ceil_ntd', 'none')),
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (statutory_min IS NULL OR statutory_max IS NULL OR statutory_min <= statutory_max),
    CHECK (formula_kind <> 'demerit_accumulation' OR demerit_threshold IS NOT NULL)
);

CREATE INDEX penalty_matrix_by_provision ON scoring.penalty_matrix (provision_id);

COMMENT ON TABLE scoring.penalty_matrix IS
    '環境法裁罰準則（法規命令）與裁量基準／記點原則（行政規則）之結構化計算矩陣；以 provision_id 與條文雜湊雙重錨定。';

CREATE TABLE scoring.matrix_factor (
    id               bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    matrix_id        bigint NOT NULL REFERENCES scoring.penalty_matrix (id) ON DELETE CASCADE,
    factor_code      text NOT NULL CHECK (factor_code ~ '^[a-zA-Z0-9_]+$'),
    factor_role      scoring.factor_role NOT NULL,
    label_zho        text NOT NULL,
    statutory_ref    text NOT NULL,
    default_value    numeric(18, 3),
    min_value        numeric(18, 3),
    max_value        numeric(18, 3),
    needs_judgement  boolean NOT NULL DEFAULT false,
    description      text NOT NULL DEFAULT '',
    UNIQUE (matrix_id, factor_code),
    CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
);

COMMENT ON TABLE scoring.matrix_factor IS
    '裁罰矩陣之各計算因子（點數項、A–E 權重因子、營建工程缺失記點項或固定金額項）。';

-- 權限授與：沿用 0027 角色分工（reader 只讀，writer 可讀寫，reviewer 只讀）
GRANT USAGE ON SCHEMA scoring TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA scoring TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA scoring TO envcite_ontology_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON vocab.concept_relation, annotation.mutatis_mutandis TO envcite_ontology_writer;
GRANT SELECT ON vocab.concept_relation, annotation.mutatis_mutandis TO envcite_ontology_reader, envcite_ontology_reviewer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA scoring TO envcite_ontology_writer;
GRANT USAGE, SELECT ON SEQUENCE annotation.mutatis_mutandis_id_seq TO envcite_ontology_writer;
