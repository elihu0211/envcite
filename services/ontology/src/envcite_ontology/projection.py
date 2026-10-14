"""檢索投影的建置。

`retrieval.lexical_index` 是可整個重建的衍生物，且為內容定址：一列是（段落文字雜湊，
分詞器識別碼）的純函式，不指向版本列。索引單位是段落，召回單位仍是條，由查詢函式依路徑歸併。

內容定址使索引無從陳舊：

- 文字改了：舊列無人引用（`retrieval.prune` 清掉），新文字缺列（`retrieval.missing_lexical`
  列出，這裡補上）。
- 效力列更替而文字沒變：雜湊相同，沿用原列。

以段落為單位，是因為以條為單位時，條下單元更正而條的列不變，會把舊文字誤判為新鮮。

分詞器識別碼含詞典（法規名稱）雜湊，新法規出現即變、舊列作廢：建置時先刪不符的列再補缺。
服務端詞典啟動時讀一次，重建後須重啟才採用新識別碼。
"""

from __future__ import annotations

import logging
from typing import cast

import psycopg

from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_PAGE = 2000

# 一頁缺列的文字；同 `retrieval.missing_lexical` 但以雜湊鍵分頁（該函式逐頁呼叫會變成
# 頁數乘以全表掃描），現行列雜湊的部分索引可自 `after` 起依序走。
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
    """捨棄失效列並補齊缺列；回傳新建列數。"""
    segmenter = Segmenter(
        [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    )
    tokenizer = segmenter.tokenizer_id
    # 刪識別碼不符的列，以及文字已無人引用的列。
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
