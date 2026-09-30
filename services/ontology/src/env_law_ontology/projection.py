"""檢索投影的建置（env-law-docs 04-01 §3、§5）。

`retrieval.article_index` 是語料的衍生物，可整個丟掉重建。索引與查詢必須用同一個分詞器，
分詞器識別碼含詞典（法規名稱）的雜湊，所以標題一變、識別碼就變，先前的列全部作廢。
因此每次建置先刪掉識別碼不符的列，再補上沒有索引的條：法規被重寫時其版本連同索引
一起被刪，補的就正好是內容改變的條。
"""

from __future__ import annotations

import logging
from itertools import batched

import psycopg

from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_BATCH = 1000

# 條的全文，與 corpus.article_text 同義（有下層者依閱讀順序組成最細單元的內容），但一次
# 對整個語料做集合式的聚合：逐條呼叫該函式要對同一部法規的每個單元掃描一遍，大法規會
# 慢到數量級。
_MISSING = """
    SELECT a.id,
           coalesce(a.content,
                    string_agg(cv.content, E'\\n' ORDER BY c.sort_key)
                        FILTER (WHERE cv.content IS NOT NULL))
      FROM corpus.provision_version AS a
      JOIN corpus.provision AS p ON p.id = a.provision_id AND p.provision_type = 'article'
      LEFT JOIN corpus.provision AS c
             ON c.resource_id = p.resource_id
            AND split_part(c.path, '/', 1) = p.path
            AND c.id <> p.id
      LEFT JOIN corpus.provision_version AS cv
             ON cv.provision_id = c.id AND cv.expression_id = a.expression_id
     WHERE NOT EXISTS (
           SELECT 1 FROM retrieval.article_index AS i WHERE i.provision_version_id = a.id
       )
     GROUP BY a.id, a.content
"""


def rebuild(conn: Conn) -> int:
    """補齊索引，回傳新建的列數。詞典取自現有的標題，與伺服器啟動時相同。"""
    lexicon = [
        str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")
    ]
    segmenter = Segmenter(lexicon)
    tokenizer = segmenter.tokenizer_id

    stale = conn.execute(
        "DELETE FROM retrieval.article_index WHERE tokenizer <> %s", (tokenizer,)
    ).rowcount
    if stale:
        log.info("分詞器識別碼改變，捨棄 %d 列舊索引", stale)

    built = 0
    with conn.cursor(name="index_missing") as source, conn.cursor() as sink:
        source.execute(_MISSING)
        for rows in batched(source, _BATCH):
            batch = [
                (vid, segmenter.to_tsvector_input(str(text)), tokenizer)
                for vid, text in rows
                if text
            ]
            sink.executemany(
                "INSERT INTO retrieval.article_index (provision_version_id, content_tsv, tokenizer)"
                " VALUES (%s, to_tsvector('simple', %s), %s)",
                batch,
            )
            built += len(batch)
    conn.commit()
    log.info("檢索投影：新建 %d 列，分詞器 %s", built, tokenizer)
    return built
