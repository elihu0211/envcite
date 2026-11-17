-- 0006_law_name_not_null — 法規名稱表不出沒有名稱的列
--
-- `guidance.law_name` 的契約是「name 唯一」的名稱表，鍵就是名稱。但標題可為空：文字缺漏的歷史版本
-- （`text_status = unavailable`，只知道版本日與時間軸，沒有標題）也是一版，視圖第一支沒濾掉它們，
-- 於是多出一列名稱為空、`pcode` 為空的列（現場 302 個沒有標題的版本聚成這一列）。消費者以名稱為鍵
-- 建表，空鍵一進去，取最長名稱就崩潰；而且它沒有任何意義：沒有名稱的列無從比對公文裡的法名。
--
-- 在視圖裡濾掉，不在消費端各自略過：契約由來源保證，消費端以非空的型別讀取、違反時直接報錯。

CREATE OR REPLACE VIEW guidance.law_name AS
 WITH named AS (
         SELECT guidance.name_key(e.title) AS name,
            r.local_id AS pcode,
            false AS alias
           FROM (corpus.legal_expression e
             JOIN corpus.legal_resource r ON ((r.id = e.resource_id)))
          WHERE (r.id_scheme = 'moj_pcode'::corpus.id_scheme) AND (e.title IS NOT NULL)
        UNION
         SELECT guidance.name_key(m.m[1]) AS name_key,
            r.local_id,
            true
           FROM ((corpus.lifecycle_event e
             JOIN corpus.legal_resource r ON ((r.id = e.resource_id)))
             CROSS JOIN LATERAL regexp_matches(guidance.name_key(e.source_text), '原名稱:([^;)]+);新名稱[:;]'::text, 'g'::text) m(m))
          WHERE ((r.id_scheme = 'moj_pcode'::corpus.id_scheme) AND (e.source_text ~~ '%原名稱%'::text))
        )
 SELECT name,
        CASE
            WHEN (count(DISTINCT pcode) = 1) THEN min(pcode)
            ELSE NULL::text
        END AS pcode,
    bool_and(alias) AS alias
   FROM named
  GROUP BY name;
