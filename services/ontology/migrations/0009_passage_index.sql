-- 0009_passage_index — 檢索投影改為內容定址、逐段落
--
-- 設計見 envcite-docs 02-04 §8 與 04-01 §2、§5。
--
-- 舊的 retrieval.article_index 以條的版本列為鍵、對條的聚合全文建索引。條的全文由其下
-- 單元組成，條下的單元更正時條本身的列不變，「有沒有索引列」的重建判準因此把更正前的
-- 文字當成新鮮（02-05 F2）：字面檢索比對的是舊文字，而 corpus.article_text 回的已是
-- 新文字，不報錯。
--
-- 新的索引以段落（有文字的最細單元）的文字雜湊為鍵，一列是（文字，分詞器）的純函式，
-- 不指向任何版本列：文字改了，舊文字的列不再被引用（prune 清掉）、新文字沒有列
-- （missing_lexical 恰好列出）；效力列更替而文字沒變，雜湊相同，原列沿用。失效只剩
-- 「缺列」一種，而缺列可以列舉。召回的單位仍是條：段落的命中依路徑歸到條。
--
-- 索引是可重建的投影，舊表直接丟棄；內容由下一次 projection.rebuild 補齊。

DROP FUNCTION retrieval.search_articles(text, text, date, text, integer);
DROP TABLE retrieval.article_index;

CREATE TABLE retrieval.lexical_index (
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    -- 分詞器識別碼：實作版本加詞典內容雜湊。不同分詞器的詞素不可比。
    tokenizer      text NOT NULL CHECK (tokenizer <> ''),
    tsv            tsvector NOT NULL,
    PRIMARY KEY (content_sha256, tokenizer)
);

-- 標準 GIN 索引：隨串流複寫到每個副本，備援切換後不必重建。
CREATE INDEX lexical_index_tsv ON retrieval.lexical_index USING gin (tsv);

-- 由段落的文字雜湊回到現行的版本列；只含現行列。
CREATE INDEX provision_version_current_by_content
    ON corpus.provision_version (content_sha256) WHERE superseded_at IS NULL;

-- 缺列的完整清單：現行的、有文字的段落中，沒有該分詞器索引列的文字。建索引只需補這些。
CREATE FUNCTION retrieval.missing_lexical(p_tokenizer text)
RETURNS TABLE (content_sha256 bytea, content text)
LANGUAGE sql STABLE
AS $$
    SELECT DISTINCT ON (v.content_sha256) v.content_sha256, v.content
      FROM corpus.provision_version AS v
     WHERE v.superseded_at IS NULL AND v.content IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM retrieval.lexical_index AS i
                        WHERE i.content_sha256 = v.content_sha256 AND i.tokenizer = p_tokenizer)
     ORDER BY v.content_sha256
$$;

-- 刪除不再被任何現行列引用的文字的索引列（更正掉的舊文字），回傳刪除列數。
CREATE FUNCTION retrieval.prune() RETURNS bigint
LANGUAGE sql
AS $$
    WITH gone AS (
        DELETE FROM retrieval.lexical_index AS i
         WHERE NOT EXISTS (SELECT 1 FROM corpus.provision_version AS v
                            WHERE v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL)
        RETURNING 1
    )
    SELECT count(*) FROM gone
$$;

-- 某日有效之條，依字面相關性排序。命中的是段落；條的分數取其下段落的最大值。只納入該日
-- 效力區間內、文字可得者；該日無有效版本的法規不以現行文字頂替。
CREATE FUNCTION retrieval.search_articles(
    p_terms     text,
    p_tokenizer text,
    p_as_of     date,
    p_language  text,
    p_limit     integer
)
RETURNS TABLE (
    version_id    bigint,
    resource_id   bigint,
    path          text,
    lexical_score real
)
LANGUAGE sql STABLE
AS $$
    WITH q AS (SELECT retrieval.terms_to_tsquery(p_terms) AS query),
    hit AS (
        SELECT p.resource_id, split_part(p.path, '/', 1) AS article_path,
               max(ts_rank_cd(i.tsv, q.query)) AS score
          FROM retrieval.lexical_index AS i
         CROSS JOIN q
          JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
          JOIN corpus.provision AS p ON p.id = v.provision_id
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
         WHERE i.tokenizer = p_tokenizer
           AND i.tsv @@ q.query
           AND v.superseded_at IS NULL
           AND v.in_force @> p_as_of
           AND e.language = p_language
           AND e.text_status = 'available'
         GROUP BY p.resource_id, split_part(p.path, '/', 1)
    )
    SELECT av.id, h.resource_id, h.article_path, h.score
      FROM hit AS h
      JOIN corpus.provision AS ap ON ap.resource_id = h.resource_id AND ap.path = h.article_path
      JOIN corpus.provision_version AS av ON av.provision_id = ap.id
                                         AND av.superseded_at IS NULL AND av.in_force @> p_as_of
      JOIN corpus.legal_expression AS ae ON ae.id = av.expression_id AND ae.language = p_language
     ORDER BY h.score DESC, av.id
     LIMIT p_limit;
$$;
