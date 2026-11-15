-- 0057_reextraction — 義務沒有定論的單元帶著審查意見重抽，輪數上限由資料庫強制
--
-- 一次抽取有隨機性：同一單元兩輪抽取，義務可能一次確認、一次被駁回（漏了期限、對象、限定語）。
-- 罰則只處罰已確認的義務，少數關鍵義務有沒有確認，決定整部法的罰則確認數。義務沒有定論的單元
-- 要再抽，並把審查者指出的問題一併給抽取者。
--
-- 「該不該重抽」由資料推得，不另存佇列：第幾輪記在 extraction_attempt，上限是 CHECK，
-- 不是程式裡的常數——程式怎麼寫，一個單元的重抽都不會超過三次。

ALTER TABLE annotation.extraction_attempt
    ADD COLUMN round smallint NOT NULL DEFAULT 0 CONSTRAINT extraction_attempt_round_range
        CHECK (round BETWEEN 0 AND 3);

COMMENT ON COLUMN annotation.extraction_attempt.round IS
    '第幾輪抽取：0 是首次，1 以上是帶審查意見的重抽（上限 3）。';

ALTER TABLE annotation.extraction_attempt DROP CONSTRAINT extraction_attempt_pkey;
ALTER TABLE annotation.extraction_attempt
    ADD PRIMARY KEY (provision_id, content_sha256, producer, producer_version, round);

-- 單元現行文字上沒有定論的義務或禁止：未確認，且沒有已確認的義務或禁止涵蓋同一段引文
-- （區間重疊視為同一段；任一邊沒有區間，引文即全文，視為涵蓋）。
CREATE VIEW annotation.unresolved_duty AS
SELECT s.id AS statement_id, s.provision_id, s.content_sha256
  FROM annotation.statement AS s
  JOIN annotation.assertion AS a ON a.id = s.id
  JOIN corpus.provision_version AS v
    ON v.provision_id = s.provision_id AND v.content_sha256 = s.content_sha256
   AND v.superseded_at IS NULL
 WHERE s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')
   AND a.confirm_state <> 'confirmed'
   AND NOT EXISTS (
       SELECT 1
         FROM annotation.statement AS c
         JOIN annotation.assertion AS ca ON ca.id = c.id
        WHERE c.provision_id = s.provision_id AND c.content_sha256 = s.content_sha256
          AND c.statement_kind = 'prescriptive' AND c.norm_modality IN ('obligation', 'prohibition')
          AND ca.confirm_state = 'confirmed'
          AND (c.span IS NULL OR s.span IS NULL OR c.span && s.span));

-- 該重抽的（單元，抽取者，版本）：首次抽取已完成，輪數沒用完，而且
--   · 仍有沒定論的義務（unresolved_duty），或
--   · 被處罰條文以「違反」引用卻沒有任何未駁回的義務（duty_gap）：抽取全被丟掉或判成非義務的關鍵義務。
-- next_round 是已做的最後一輪加一。
CREATE VIEW annotation.reextraction_due AS
SELECT a.provision_id, a.content_sha256, a.producer, a.producer_version,
       (max(a.round) + 1)::smallint AS next_round
  FROM annotation.extraction_attempt AS a
  JOIN corpus.provision AS up ON up.id = a.provision_id
 WHERE EXISTS (SELECT 1 FROM annotation.unresolved_duty AS u
                WHERE u.provision_id = a.provision_id AND u.content_sha256 = a.content_sha256)
    OR EXISTS (SELECT 1
                 FROM annotation.duty_gap AS g
                 JOIN corpus.provision AS gp ON gp.id = g.provision_id
                WHERE gp.resource_id = up.resource_id
                  AND (up.path = gp.path OR up.path LIKE gp.path || '/%'))
 GROUP BY a.provision_id, a.content_sha256, a.producer, a.producer_version
HAVING max(a.round) < 3;

COMMENT ON VIEW annotation.unresolved_duty IS
    '單元現行文字上沒有定論的義務或禁止：未確認，且沒有已確認的義務或禁止涵蓋同一段引文。';
COMMENT ON VIEW annotation.reextraction_due IS
    '義務沒有定論，或被處罰條文引用卻沒有義務，且還有輪數可重抽的（單元，抽取者，版本）；next_round 是下一輪的編號。';

GRANT SELECT ON annotation.unresolved_duty, annotation.reextraction_due
    TO envcite_ontology_reader, envcite_ontology_writer, envcite_ontology_reviewer;
