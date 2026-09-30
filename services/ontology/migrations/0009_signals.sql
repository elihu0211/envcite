-- 0009_signals — 觀測信號（env-law-docs 03-04 §7）
--
-- 每個信號一列（對象、信號、是否觸發、說明），對象是來源或全量檔。任何監控工具（
-- sql_exporter、Grafana、一個定時的查詢）都只需 `SELECT * FROM source.signal WHERE firing`。
-- 閾值寫在此處：36 小時無成功、14 天無新快照、驗收比例達上限的八成、單次異動超過法規
-- 總數的 1%、待查逾七天。Job 失敗與成功過期另有 Kubernetes 的標準指標，這裡的同名信號
-- 是沒有指標時的替代。
--
-- 待查（corrected、version_regressed）的解除方式是人查看之後刪掉該列問題；匯入時每部
-- 法規的問題整批重寫，內容再改變也會自然更新。

CREATE VIEW source.signal AS
WITH latest AS (
    SELECT DISTINCT ON (source) *
      FROM source.run
     WHERE finished_at IS NOT NULL
     ORDER BY source, started_at DESC
), last_ok AS (
    SELECT source, max(finished_at) AS at
      FROM source.run
     WHERE outcome IN ('success', 'unchanged')
     GROUP BY source
), ratios AS (
    SELECT l.source,
           bool_or((v.value ->> 'value')::float8 >= 0.8 * (v.value ->> 'limit')::float8) AS firing,
           string_agg(
               format('%s %s/%s', v.key, round((v.value ->> 'value')::numeric, 4), v.value ->> 'limit'),
               '；' ORDER BY v.key
           ) AS detail
      FROM latest AS l
     CROSS JOIN LATERAL jsonb_each(l.ratios) AS v
     GROUP BY l.source
)
SELECT source AS subject, 'last_run_failed' AS signal,
       outcome IN ('failed', 'rejected') AS firing,
       format('%s，結束碼 %s，停在 %s', outcome, exit_code, step) AS detail
  FROM latest
UNION ALL
SELECT s.source, 'success_stale',
       coalesce(o.at, '-infinity') < now() - interval '36 hours',
       coalesce('最後一次成功 ' || o.at, '從未成功')
  FROM (SELECT DISTINCT source FROM source.run) AS s
  LEFT JOIN last_ok AS o USING (source)
UNION ALL
SELECT feed::text, 'source_stalled',
       max(fetched_at) < now() - interval '14 days',
       '最後一份新快照 ' || max(fetched_at)
  FROM source.snapshot
 GROUP BY feed
UNION ALL
SELECT source, 'ratio_near_limit', firing, detail FROM ratios
UNION ALL
SELECT source, 'version_surge',
       coalesce(laws_versioned, 0) + coalesce(laws_corrected, 0)
           > 0.01 * (SELECT count(*) FROM corpus.legal_resource),
       format('新增版本 %s、更正 %s', laws_versioned, laws_corrected)
  FROM latest
UNION ALL
SELECT f.feed::text, 'review_backlog',
       count(p.id) > 0,
       format('%s 部法規的待查逾七天', count(p.id))
  FROM (SELECT DISTINCT feed FROM source.snapshot) AS f
  LEFT JOIN source.snapshot AS sn
         ON sn.feed = f.feed AND sn.fetched_at < now() - interval '7 days'
  LEFT JOIN source.import_problem AS p
         ON p.snapshot_id = sn.id AND p.code IN ('corrected', 'version_regressed')
 GROUP BY f.feed;
