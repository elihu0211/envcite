"""檢索投影的建置（env-law-docs 04-01 §3、§5；02-04 §8）。

`retrieval.lexical_index` 是語料的衍生物，可整個丟掉重建，而且是**內容定址**的：一列是
（段落文字的雜湊，分詞器識別碼）的純函式，不指向任何版本列。索引的單位是段落，也就是有
文字的最細單元；召回的單位仍是條，由查詢函式依路徑歸併。

內容定址使索引無從陳舊：

- 文字改了：舊文字的列不再被任何現行列引用（`retrieval.prune` 清掉），新文字沒有列
  （`retrieval.missing_lexical` 列出，這裡補上）。
- 效力列更替而文字沒變：新列帶著同一個雜湊，原有的列沿用，不必重建。

初版以條為單位、以版本列編號為鍵，條下單元更正時條本身的列不變，「有沒有索引列」的判準
把更正前的文字當成新鮮；以段落為單位則沒有任何聚合出來的全文可以與其組成部分分岔。

索引與查詢必須用同一個分詞器，分詞器識別碼含詞典（法規名稱）的雜湊，所以新法規一出現
識別碼就變、舊識別碼的列作廢：建置時先刪掉識別碼不符的列，再補上缺的列。服務端的詞典
啟動時讀一次，重建之後要重新啟動服務才用得上新識別碼。
"""

from __future__ import annotations

import logging
from typing import cast

import psycopg

from env_law_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_PAGE = 2000

# 一頁缺列的文字。與 `retrieval.missing_lexical` 同義，但以雜湊為鍵分頁：該函式每次都要掃
# 整個缺列集合，逐頁呼叫會把一次全建變成頁數乘以全表掃描。現行列的雜湊有部分索引，
# 自 `after` 起依序走索引即可。
_PAGE_SQL = """
    SELECT DISTINCT ON (v.content_sha256) v.content_sha256, v.content
      FROM corpus.provision_version AS v
     WHERE v.superseded_at IS NULL
       AND v.content IS NOT NULL
       AND v.content_sha256 > %(after)s
       AND NOT EXISTS (
           SELECT 1 FROM retrieval.lexical_index AS i
            WHERE i.content_sha256 = v.content_sha256 AND i.tokenizer = %(tokenizer)s
       )
     ORDER BY v.content_sha256
     LIMIT %(limit)s
"""


def rebuild(conn: Conn) -> int:
    """補齊索引；回傳新建的列數。詞典取自現有的標題。"""
    segmenter = Segmenter(
        [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    )
    tokenizer = segmenter.tokenizer_id
    # 識別碼不符的列，與不再被任何現行列引用的文字的列。
    stale = conn.execute(
        "DELETE FROM retrieval.lexical_index WHERE tokenizer <> %s", (tokenizer,)
    ).rowcount
    pruned = conn.execute("SELECT retrieval.prune()").fetchone()
    conn.commit()
    if stale:
        log.info("分詞器識別碼改變，捨棄 %d 列舊索引", stale)
    if pruned and pruned[0]:
        log.info("清理 %s 列不再被引用的文字的索引", pruned[0])

    built = _build(conn, segmenter)
    log.info("檢索投影：新建 %d 列，分詞器 %s", built, tokenizer)
    return built


def _build(conn: Conn, segmenter: Segmenter) -> int:
    tokenizer = segmenter.tokenizer_id
    built = 0
    after = b""
    while True:
        rows = conn.execute(
            _PAGE_SQL, {"after": after, "tokenizer": tokenizer, "limit": _PAGE}
        ).fetchall()
        if not rows:
            return built
        batch = [
            (digest, tokenizer, segmenter.to_tsvector_input(str(text)))
            for digest, text in rows
            if text
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO retrieval.lexical_index (content_sha256, tokenizer, tsv)"
                " VALUES (%s, %s, to_tsvector('simple', %s))"
                " ON CONFLICT DO NOTHING",
                batch,
            )
        conn.commit()
        built += len(batch)
        after = cast(bytes, rows[-1][0])
