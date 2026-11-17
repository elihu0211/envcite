-- 0005_sanctions_at — 罰則所罰的義務依查詢日解析
--
-- 罰則條文「違反第 N 條規定者」指的是第 N 條在該日的規定（中央法規標準法第 17 條）。
-- annotation.sanction 的連結寫入後不改，指向連結當時的義務陳述；義務條文修正而罰則條文沒改時，
-- 修正後的義務是內容雜湊不同的另一個陳述，該日的有效陳述裡沒有舊的那一個。連結保留原樣（修正前的
-- 日子仍用它），讀取時以受罰條文單元加查詢日找該日的義務。
--
-- 只在一對一時改指：舊陳述是它那份文字裡唯一的義務（連結不是從數個之中挑的），且該日的單元恰有
-- 一個義務。任一邊有數個時，挑哪一個是判斷，留給連結步驟，這裡維持原連結。

CREATE FUNCTION annotation.sanctions_at(statement_ids bigint[], p_language text, p_on date)
    RETURNS TABLE(statement_id bigint, punished_statement_id bigint, legal_effect text,
                  amount_text text, min_amount numeric, max_amount numeric, per_occasion boolean,
                  discretionary boolean)
    LANGUAGE sql STABLE
    AS $$
    WITH s AS (
        SELECT * FROM annotation.sanctions_of(statement_ids)
    ),
    -- 受罰陳述不在這批之內、且是它那份文字裡唯一被接受的義務
    moved AS (
        SELECT s.statement_id, p.provision_id, p.content_sha256, pr.resource_id
          FROM s
          JOIN annotation.statement AS p ON p.id = s.punished_statement_id
          JOIN corpus.provision AS pr ON pr.id = p.provision_id
         WHERE NOT p.id = ANY(statement_ids)
           AND p.norm_modality IN ('obligation', 'prohibition')
           AND NOT EXISTS (
               SELECT 1
                 FROM annotation.statement AS o
                 JOIN annotation.assertion AS a ON a.id = o.id
                 JOIN annotation.derivation AS d ON d.id = a.derivation_id
                WHERE o.provision_id = p.provision_id AND o.content_sha256 = p.content_sha256
                  AND o.id <> p.id
                  AND o.statement_kind = 'prescriptive'
                  AND o.norm_modality IN ('obligation', 'prohibition')
                  AND a.confirm_state <> 'rejected'
                  AND (a.confirm_state = 'confirmed' OR d.method = 'rule'))
    ),
    live AS (
        SELECT r.statement_id
          FROM (SELECT DISTINCT resource_id FROM moved) AS l
         CROSS JOIN LATERAL (SELECT * FROM annotation.statement_versions_at(l.resource_id, p_language, p_on)
                             OFFSET 0) AS r
    ),
    -- 該日單元的文字與舊陳述不同（修正了）時，該日單元裡唯一的義務
    successor AS (
        SELECT m.statement_id, min(n.id) AS successor_id
          FROM moved AS m
          JOIN annotation.statement AS n
            ON n.provision_id = m.provision_id AND n.content_sha256 <> m.content_sha256
          JOIN live ON live.statement_id = n.id
         WHERE n.statement_kind = 'prescriptive'
           AND n.norm_modality IN ('obligation', 'prohibition')
         GROUP BY m.statement_id
        HAVING count(*) = 1
    )
    SELECT s.statement_id, coalesce(x.successor_id, s.punished_statement_id), s.legal_effect,
           s.amount_text, s.min_amount, s.max_amount, s.per_occasion, s.discretionary
      FROM s
      LEFT JOIN successor AS x ON x.statement_id = s.statement_id
$$;

COMMENT ON FUNCTION annotation.sanctions_at(statement_ids bigint[], p_language text, p_on date) IS '這批陳述的法律效果，所罰的陳述依該日解析：原連結的陳述該日不在這批之內、其單元已修正，且新舊兩邊各恰有一個義務時，改指該日的義務；否則為原連結。是否只留罰則指向這批陳述之內，由呼叫端決定。';
