-- 0020_health_views — 標註管線的健康，一眼看得到
--
-- 推論能不能用，取決於有多少陳述通過驗證；閘門的通過率是這個系統最核心的健康指標。
-- 這兩個檢視讓它能直接被查（psql、儀表板的資料來源、服務的指標回呼），不必另存統計。

CREATE VIEW annotation.statement_health AS
SELECT confirm_state::text AS state, count(*) AS statements
  FROM annotation.statement
 GROUP BY confirm_state;

COMMENT ON VIEW annotation.statement_health IS
    '各審核狀態的陳述數：extracted 待驗證、confirmed 可進推論、rejected 已駁回。';

-- 每個陳述每個方法取最近一次驗證（以編號為準，同 annotation.settle）；政策列不算閘門。
CREATE VIEW annotation.gate_health AS
SELECT method, verdict::text AS verdict, count(*) AS statements
  FROM (
        SELECT DISTINCT ON (statement_id, method) statement_id, method, verdict
          FROM annotation.verification
         WHERE method <> 'policy'
         ORDER BY statement_id, method, id DESC
       ) AS latest
 GROUP BY method, verdict;

COMMENT ON VIEW annotation.gate_health IS
    '各驗證方法最近一次的結果分布：pass、fail、inconclusive、not_applicable。';
