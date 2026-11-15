-- 0042_delegation — 規範關係「授權」：命令依哪一部法律的哪一條訂定
--
-- 授權是法律條文指向依其訂定之法規命令的關係（行政程序法第 150 條第 2 項：法規命令應明列其
-- 法律授權之依據），是授權鏈的一環：命令附表的限值因此接到母法的義務。關係只寫在條文裡，
-- 全國法規資料庫沒有欄位，所以全部由抽取而得。
--
-- - 授權是主張的一種（assertion_kind = delegation）：沿用推導活動、封存、驗證紀錄與確認狀態，
--   「規則推導或已確認才可用、駁回不可用」的守門與陳述、用語定義相同。0016 拿掉的規範關係表
--   當時沒有生產者；現在有（delegation.extract 以規則從命令首條抽取），故加回。
-- - 命令端錨定（法規，條文單元，內容雜湊），與陳述同一套：命令的文字改了，授權不再自動沿用，
--   須重新抽取。條文單元可空（公報說明的「依據：」只指向命令整體），空時雜湊也空，命令在該日
--   有任一單元現行即適用。
-- - 法律端記「原文怎麼寫」（cited_law_name、cited_path）與「對到了什麼」（law_resource_id、
--   law_provision_id）。法名只對語料內已有的法規名稱（任一版本的標題，且對到唯一一部），對不到、
--   對到數部、或對到法規但沒有該條文單元，都留空而不猜：空即「授權不明」，由讀取的一方判斷。
--   款記到所屬的項（款是項的一部分，授權條文是那一項）。
-- - 授權不固定目標版本：不存版本，依查詢日解析（delegations_at、delegated_by 回報法律端
--   該日是否現行）。
-- - 同一處引文先前對不到、之後語料補齊而對到時，新對到的是另一個主張（內容不同）；
--   delegation_usable 讓已對到者遮住同一處引文對不到的舊列。

CREATE TABLE annotation.delegation (
    id                     bigint PRIMARY KEY,
    command_resource_id    bigint NOT NULL REFERENCES corpus.legal_resource ON DELETE CASCADE,
    command_provision_id   bigint,
    command_content_sha256 bytea CHECK (octet_length(command_content_sha256) = 32),
    cited_law_name         text NOT NULL CHECK (cited_law_name = btrim(cited_law_name) AND cited_law_name <> ''),
    cited_path             text NOT NULL
        CHECK (cited_path ~ '^art_[0-9]+(-[0-9]+)*(/para_[0-9]+)?$'),
    law_resource_id        bigint REFERENCES corpus.legal_resource ON DELETE CASCADE,
    law_provision_id       bigint,
    assertion_kind         annotation.assertion_kind
        GENERATED ALWAYS AS ('delegation'::annotation.assertion_kind) STORED,
    CONSTRAINT delegation_is_assertion
        FOREIGN KEY (id, assertion_kind) REFERENCES annotation.assertion (id, assertion_kind) ON DELETE CASCADE,
    CONSTRAINT delegation_command_provision
        FOREIGN KEY (command_provision_id, command_resource_id)
        REFERENCES corpus.provision (id, resource_id) ON DELETE CASCADE,
    CONSTRAINT delegation_law_provision
        FOREIGN KEY (law_provision_id, law_resource_id)
        REFERENCES corpus.provision (id, resource_id) ON DELETE CASCADE,
    CONSTRAINT delegation_anchor_whole CHECK ((command_provision_id IS NULL) = (command_content_sha256 IS NULL)),
    CONSTRAINT delegation_law_before_provision CHECK (law_provision_id IS NULL OR law_resource_id IS NOT NULL),
    CONSTRAINT delegation_not_self CHECK (law_resource_id IS DISTINCT FROM command_resource_id)
);

COMMENT ON TABLE annotation.delegation IS
    '授權：命令（命令端）依法律（法律端）某條文訂定。不固定目標版本，依查詢日解析。';
COMMENT ON COLUMN annotation.delegation.law_resource_id IS
    '法名對到的法規；對不到（無此名稱、對到數部）為空，不猜。';
COMMENT ON COLUMN annotation.delegation.law_provision_id IS
    '授權條文單元；法規對到了但沒有該條文單元時為空。';

CREATE INDEX delegation_by_command ON annotation.delegation (command_resource_id, command_provision_id);
CREATE INDEX delegation_by_law ON annotation.delegation (law_resource_id) WHERE law_resource_id IS NOT NULL;

CREATE TRIGGER delegation_content_after_seal
    BEFORE INSERT OR UPDATE ON annotation.delegation
    FOR EACH ROW EXECUTE FUNCTION annotation.content_after_seal('id');

CREATE TRIGGER delegation_drops_assertion
    AFTER DELETE ON annotation.delegation
    FOR EACH ROW EXECUTE FUNCTION annotation.drop_assertion();

-- 指紋：授權的內容是命令端錨點、原文引用與對到的結果

CREATE OR REPLACE FUNCTION annotation.assertion_fingerprint(p_assertion bigint)
 RETURNS bytea
 LANGUAGE sql
 STABLE
 SET search_path TO 'pg_catalog', 'pg_temp'
AS $function$
    SELECT sha256(convert_to(
        CASE c.assertion_kind
        WHEN 'statement' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(s.provision_id, encode(s.content_sha256, 'hex')),
                'kind', s.statement_kind,
                'modality', s.norm_modality,
                'slots', coalesce((
                    SELECT jsonb_agg(jsonb_build_array(sl.slot_role, sl.concept_id, sl.literal,
                                                       sl.liability_basis)
                                     ORDER BY sl.slot_role, sl.concept_id, sl.literal, sl.liability_basis)
                      FROM annotation.statement_slot AS sl WHERE sl.statement_id = s.id), '[]'),
                'conditions', coalesce((
                    SELECT jsonb_agg(jsonb_build_array(
                               k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                               k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                               k.delegate_provision_id, k.needs_assessment,
                               k.delegate_permit_concept_id, k.measurement_method_concept_id,
                               k.averaging_period_concept_id)
                           ORDER BY k.condition_role, k.disjunct_no, k.concept_id, k.comparator,
                                    k.threshold, k.measure_unit, k.value_concept_id, k.negated,
                                    k.delegate_provision_id, k.needs_assessment,
                                    k.delegate_permit_concept_id, k.measurement_method_concept_id,
                                    k.averaging_period_concept_id)
                      FROM annotation.statement_condition AS k WHERE k.statement_id = s.id), '[]'),
                'sanction', (
                    SELECT jsonb_build_array(n.punished_statement_id, n.legal_effect, n.min_amount,
                                             n.max_amount, n.per_occasion, n.discretionary)
                      FROM annotation.sanction AS n WHERE n.statement_id = s.id))
              FROM annotation.statement AS s WHERE s.id = c.id)
        WHEN 'definition' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(t.provision_id, encode(t.content_sha256, 'hex')),
                'term', t.term_concept_id,
                'members', coalesce((
                    SELECT jsonb_agg(m.member_concept_id ORDER BY m.member_concept_id)
                      FROM annotation.term_definition_member AS m WHERE m.definition_id = t.id), '[]'))
              FROM annotation.term_definition AS t WHERE t.id = c.id)
        WHEN 'delegation' THEN (
            SELECT jsonb_build_object(
                'anchor', jsonb_build_array(g.command_resource_id, g.command_provision_id,
                                            encode(g.command_content_sha256, 'hex')),
                'cited', jsonb_build_array(g.cited_law_name, g.cited_path),
                'law', jsonb_build_array(g.law_resource_id, g.law_provision_id))
              FROM annotation.delegation AS g WHERE g.id = c.id)
        END::text, 'UTF8'))
      FROM annotation.assertion AS c
     WHERE c.id = p_assertion
$function$;

-- 可用的授權：未駁回，且（已確認或規則推導）；已對到者遮住同一處引文對不到的舊列。
-- 模型抽取而未確認的不可用，與陳述、用語定義同。

CREATE VIEW annotation.delegation_usable AS
SELECT g.*
  FROM annotation.delegation AS g
  JOIN annotation.assertion AS c ON c.id = g.id
  JOIN annotation.derivation AS d ON d.id = c.derivation_id
 WHERE c.confirm_state <> 'rejected'
   AND (c.confirm_state = 'confirmed' OR d.method = 'rule')
   AND NOT (
       g.law_resource_id IS NULL
       AND EXISTS (
           SELECT 1
             FROM annotation.delegation AS h
             JOIN annotation.assertion AS hc ON hc.id = h.id
             JOIN annotation.derivation AS hd ON hd.id = hc.derivation_id
            WHERE h.command_resource_id = g.command_resource_id
              AND h.command_provision_id IS NOT DISTINCT FROM g.command_provision_id
              AND h.command_content_sha256 IS NOT DISTINCT FROM g.command_content_sha256
              AND h.cited_law_name = g.cited_law_name
              AND h.cited_path = g.cited_path
              AND h.law_resource_id IS NOT NULL
              AND hc.confirm_state <> 'rejected'
              AND (hc.confirm_state = 'confirmed' OR hd.method = 'rule')))
;

-- 命令端在該日是否適用：錨定的單元在該日現行且文字雜湊相符；無錨定者命令在該日有任一單元現行
CREATE FUNCTION annotation.delegation_command_in_force(
    p_resource bigint, p_provision bigint, p_sha256 bytea, p_language text, p_on date
) RETURNS boolean
LANGUAGE sql STABLE
AS $$
    SELECT CASE WHEN p_provision IS NULL THEN
        EXISTS (SELECT 1 FROM corpus.provision_version AS v
                 WHERE v.resource_id = p_resource
                   AND v.language = p_language AND v.superseded_at IS NULL AND v.force_period @> p_on)
    ELSE
        EXISTS (SELECT 1 FROM corpus.provision_version AS v
                 WHERE v.provision_id = p_provision
                   AND v.language = p_language AND v.superseded_at IS NULL AND v.force_period @> p_on
                   AND v.content_sha256 = p_sha256)
    END
$$;

-- 某命令在某日的授權依據（B1、B5）。law_resource_id 或 law_provision_id 為空是授權不明；
-- law_status 不是 in_force 是該日法律端的條文不現行（尚未施行、已不現行等）。
-- 命令沒有任何一列，也是授權不明：抽取不到引文，不等於有授權。
CREATE FUNCTION annotation.delegations_at(p_command bigint, p_language text, p_on date)
RETURNS TABLE(
    delegation_id        bigint,
    command_provision_id bigint,
    cited_law_name       text,
    cited_path           text,
    law_resource_id      bigint,
    law_local_id         text,
    law_provision_id     bigint,
    law_version_id       bigint,
    law_status           corpus.provision_status
)
LANGUAGE sql STABLE
AS $$
    SELECT g.id, g.command_provision_id, g.cited_law_name, g.cited_path,
           g.law_resource_id, lr.local_id, g.law_provision_id, a.version_id, a.status
      FROM annotation.delegation_usable AS g
      LEFT JOIN corpus.legal_resource AS lr ON lr.id = g.law_resource_id
      LEFT JOIN LATERAL corpus.provisions_at(g.law_resource_id, p_language, p_on) AS a
           ON a.provision_id = g.law_provision_id
     WHERE g.command_resource_id = p_command
       AND annotation.delegation_command_in_force(
                g.command_resource_id, g.command_provision_id, g.command_content_sha256, p_language, p_on)
     ORDER BY g.cited_path, g.id
$$;

-- 某法在某日的授權命令：依該法授權訂定、且命令在該日適用者
CREATE FUNCTION annotation.delegated_by(p_law bigint, p_language text, p_on date)
RETURNS TABLE(
    delegation_id        bigint,
    command_resource_id  bigint,
    command_local_id     text,
    command_provision_id bigint,
    law_provision_id     bigint,
    law_version_id       bigint,
    law_status           corpus.provision_status
)
LANGUAGE sql STABLE
AS $$
    SELECT g.id, g.command_resource_id, cr.local_id, g.command_provision_id,
           g.law_provision_id, a.version_id, a.status
      FROM annotation.delegation_usable AS g
      JOIN corpus.legal_resource AS cr ON cr.id = g.command_resource_id
      LEFT JOIN corpus.provisions_at(p_law, p_language, p_on) AS a
           ON a.provision_id = g.law_provision_id
     WHERE g.law_resource_id = p_law
       AND annotation.delegation_command_in_force(
                g.command_resource_id, g.command_provision_id, g.command_content_sha256, p_language, p_on)
     ORDER BY cr.local_id, g.id
$$;
