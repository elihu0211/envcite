"""檢索投影的建置與換版（env-law-docs 04-01 §3、§5，03-04 §5）。

`retrieval.article_index` 是語料的衍生物，可整個丟掉重建。索引與查詢必須用同一個分詞器，
分詞器識別碼含詞典（法規名稱）的雜湊，所以新法規一出現識別碼就變。換版分三步，任何時刻
至少有一個識別碼的列涵蓋全部現行條文版本，服務端沒有查不到的空窗：

1. 補建：為新識別碼補上所有現行條的列，可分批提交、可中斷續跑。
2. 翻轉：單一交易把現用旗標移到新識別碼。
3. 回收：舊識別碼的列延後一個週期，於下一次建置開頭刪除，使尚未切換的服務仍有列可查。
"""

from __future__ import annotations

import logging

import psycopg

from env_law_ontology.locks import advisory
from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_PAGE = 2000

# 一頁還沒有新識別碼的列的現行條，連同其全文。全文與 corpus.article_text 同義（有下層者
# 依閱讀順序組成最細單元的內容），但對整頁一次聚合：逐條呼叫該函式要對同一部法規的每個
# 單元掃描一遍，大法規會慢到數量級。
_PAGE_SQL = """
    WITH page AS (
        SELECT v.id, v.expression_id, p.resource_id, p.path
          FROM corpus.provision_version AS v
          JOIN corpus.provision AS p ON p.id = v.provision_id AND p.provision_type = 'article'
         WHERE v.superseded_at IS NULL
           AND v.id > %(after)s
           AND NOT EXISTS (
               SELECT 1 FROM retrieval.article_index AS i
                WHERE i.provision_version_id = v.id AND i.tokenizer = %(tokenizer)s
           )
         ORDER BY v.id
         LIMIT %(limit)s
    )
    SELECT pg.id,
           coalesce(av.content,
                    string_agg(cv.content, E'\\n' ORDER BY c.sort_key)
                        FILTER (WHERE cv.content IS NOT NULL))
      FROM page AS pg
      JOIN corpus.provision_version AS av ON av.id = pg.id
      LEFT JOIN corpus.provision AS c
             ON c.resource_id = pg.resource_id
            AND split_part(c.path, '/', 1) = pg.path
            AND c.path <> pg.path
      LEFT JOIN corpus.provision_version AS cv
             ON cv.provision_id = c.id
            AND cv.expression_id = pg.expression_id
            AND cv.superseded_at IS NULL
     GROUP BY pg.id, av.content
     ORDER BY pg.id
"""


def rebuild(conn: Conn) -> int:
    """補齊索引並在完成後翻轉現用識別碼；回傳新建的列數。詞典取自現有的標題。

    投影一次只有一個建置者：各來源匯入完都要補索引。
    """
    with advisory(conn, "projection"):
        return _rebuild(conn)


def _rebuild(conn: Conn) -> int:
    segmenter = Segmenter(
        [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    )
    tokenizer = segmenter.tokenizer_id
    _recycle(conn, tokenizer)

    conn.execute(
        "INSERT INTO retrieval.projection (tokenizer, terms) VALUES (%s, %s)"
        " ON CONFLICT (tokenizer) DO NOTHING",
        (tokenizer, list(segmenter.terms)),
    )
    conn.commit()

    built = _build(conn, segmenter)
    _flip(conn, tokenizer)
    log.info("檢索投影：新建 %d 列，分詞器 %s", built, tokenizer)
    return built


def _recycle(conn: Conn, tokenizer: str) -> None:
    """回收：被取代的版本的列，以及上一輪換版留下的舊識別碼（保留現用與這一輪要建的）。"""
    conn.execute(
        "DELETE FROM retrieval.article_index AS i USING corpus.provision_version AS v"
        " WHERE v.id = i.provision_version_id AND v.superseded_at IS NOT NULL"
    )
    conn.execute(
        "DELETE FROM retrieval.article_index WHERE tokenizer NOT IN"
        " (SELECT tokenizer FROM retrieval.projection WHERE active OR tokenizer = %s)"
        "  AND tokenizer <> %s",
        (tokenizer, tokenizer),
    )
    conn.execute(
        "DELETE FROM retrieval.projection WHERE NOT active AND tokenizer <> %s", (tokenizer,)
    )
    conn.commit()


def _build(conn: Conn, segmenter: Segmenter) -> int:
    tokenizer = segmenter.tokenizer_id
    built, after = 0, 0
    while True:
        rows = conn.execute(
            _PAGE_SQL, {"after": after, "tokenizer": tokenizer, "limit": _PAGE}
        ).fetchall()
        if not rows:
            return built
        batch = [
            (vid, segmenter.to_tsvector_input(str(text)), tokenizer) for vid, text in rows if text
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO retrieval.article_index (provision_version_id, content_tsv, tokenizer)"
                " VALUES (%s, to_tsvector('simple', %s), %s)"
                " ON CONFLICT DO NOTHING",
                batch,
            )
        conn.commit()
        built += len(batch)
        after = int(rows[-1][0])  # type: ignore[call-overload]


def _flip(conn: Conn, tokenizer: str) -> None:
    """現用旗標在單一交易中移到新識別碼；已是現用者不動。"""
    with conn.transaction():
        row = conn.execute(
            "SELECT active FROM retrieval.projection WHERE tokenizer = %s", (tokenizer,)
        ).fetchone()
        if row is not None and row[0]:
            return
        # 部分唯一索引逐列檢查，先關舊的再開新的。
        conn.execute("UPDATE retrieval.projection SET active = false WHERE active")
        conn.execute(
            "UPDATE retrieval.projection SET active = true WHERE tokenizer = %s", (tokenizer,)
        )
    conn.commit()
