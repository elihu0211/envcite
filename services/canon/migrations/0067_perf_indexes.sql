-- 0067_perf_indexes — 毫秒級查詢與知識圖譜效能索引
--
-- 為高頻時間範圍查詢（legal_expression、provision_version）、法規歷程（lifecycle_event）、
-- 授權關係（delegation）與多維限值解析（statement_condition）建立部分覆蓋索引與 GiST 索引，
-- 讓 _LIST_LAWS、_GRAPH_RESOURCES、_GRAPH_NODE_DETAILS、_GRAPH_EDGE_DETAILS 與
-- annotation.threshold_sources_at / delegation_command_in_force 走 Index-Only Scan / GiST 索引。

CREATE INDEX IF NOT EXISTS legal_expression_available_at_gist
    ON corpus.legal_expression USING gist (language, text_period)
    WHERE text_status = 'available';

CREATE INDEX IF NOT EXISTS legal_expression_available_by_resource
    ON corpus.legal_expression (resource_id, language)
    INCLUDE (title, text_period)
    WHERE text_status = 'available';

CREATE INDEX IF NOT EXISTS lifecycle_event_enactment_by_resource
    ON corpus.lifecycle_event (resource_id, event_date)
    WHERE event_kind = 'enactment';

CREATE INDEX IF NOT EXISTS provision_version_current_by_prov_sha_lang
    ON corpus.provision_version (provision_id, content_sha256, language)
    INCLUDE (force_period)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS provision_version_current_by_resource_lang_gist
    ON corpus.provision_version USING gist (resource_id, language, force_period)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS delegation_usable_law_cmd
    ON annotation.delegation (law_resource_id, command_resource_id)
    INCLUDE (command_provision_id, command_content_sha256, law_provision_id, cited_path)
    WHERE law_resource_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS statement_condition_limit_by_stmt_concept
    ON annotation.statement_condition (statement_id, concept_id)
    INCLUDE (threshold, measurement_method_concept_id, averaging_period_concept_id)
    WHERE condition_role = 'limit' AND threshold IS NOT NULL;
