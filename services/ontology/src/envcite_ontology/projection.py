"""檢索投影的建置。

`retrieval.passage` 是可整個重建的衍生物，且為內容定址：一列是一段段落文字（以雜湊為鍵）的
字面向量與嵌入向量，各記產生它的分詞器與模型，不指向版本列。索引單位是段落，召回單位仍是條，
由查詢函式依路徑歸併。

內容定址使索引無從陳舊：

- 文字改了：舊列無人引用（`retrieval.prune` 清掉），新文字沒有列，這裡補上。
- 效力列更替而文字沒變：雜湊相同，沿用原列。

分詞器識別碼含詞典（法規名稱）雜湊，新法規出現即變：不符的列就地重算字面向量，嵌入不動。
模型換了同理，只重算嵌入。服務端詞典啟動時讀一次，重建後須重啟才採用新識別碼。
"""

from __future__ import annotations

import logging
from typing import cast

import psycopg

from envcite_ontology.embedding import Embedder, to_sql
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_PAGE = 2000
#: 一次送給嵌入端點的段落數
_EMBED_BATCH = 32

# 一頁要重算字面向量的文字：沒有列，或分詞器不符。以雜湊鍵分頁，現行列雜湊的部分索引可自
# `after` 起依序走（不用一次性的「全部缺列」查詢：逐頁呼叫會變成頁數乘以全表掃描）。
_LEXICAL_PAGE = """
    SELECT DISTINCT ON (v.content_sha256) v.content_sha256, v.content
      FROM corpus.provision_version AS v
      LEFT JOIN retrieval.passage AS i ON i.content_sha256 = v.content_sha256
     WHERE v.superseded_at IS NULL
       AND v.content IS NOT NULL
       AND v.content_sha256 > %(after)s
       AND (i.content_sha256 IS NULL OR i.tokenizer <> %(tokenizer)s)
     ORDER BY v.content_sha256
     LIMIT %(limit)s
"""

# 一頁要（重新）嵌入的段落：尚未嵌入，或模型不符。列一定已在（字面臂先建）。
_EMBED_PAGE = """
    SELECT DISTINCT ON (i.content_sha256) i.content_sha256, v.content
      FROM retrieval.passage AS i
      JOIN corpus.provision_version AS v
        ON v.content_sha256 = i.content_sha256 AND v.superseded_at IS NULL
     WHERE i.content_sha256 > %(after)s
       AND i.model IS DISTINCT FROM %(model)s
       AND v.content IS NOT NULL
     ORDER BY i.content_sha256
     LIMIT %(limit)s
"""


def rebuild(conn: Conn, embedder: Embedder | None = None) -> int:
    """清掉無人引用的列、補齊字面向量，有嵌入模型時再補嵌入；回傳新建或重算的字面列數。"""
    segmenter = Segmenter(
        [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    )
    pruned = conn.execute("SELECT retrieval.prune()").fetchone()
    conn.commit()
    if pruned and pruned[0]:
        log.info("清理 %s 列不再被引用的文字的索引", pruned[0])

    built = _lexical(conn, segmenter)
    log.info("檢索投影：字面向量新建或重算 %d 列，分詞器 %s", built, segmenter.tokenizer_id)
    if embedder is not None:
        embedded = _embed(conn, embedder)
        log.info("檢索投影：嵌入 %d 段，模型 %s", embedded, embedder.model)
    return built


def _lexical(conn: Conn, segmenter: Segmenter) -> int:
    tokenizer = segmenter.tokenizer_id
    built = 0
    after = b""
    while True:
        rows = conn.execute(
            _LEXICAL_PAGE, {"after": after, "tokenizer": tokenizer, "limit": _PAGE}
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
                "INSERT INTO retrieval.passage (content_sha256, tokenizer, tsv)"
                " VALUES (%s, %s, to_tsvector('simple', %s))"
                " ON CONFLICT (content_sha256)"
                " DO UPDATE SET tokenizer = EXCLUDED.tokenizer, tsv = EXCLUDED.tsv",
                batch,
            )
        conn.commit()
        built += len(batch)
        after = cast(bytes, rows[-1][0])


def _embed(conn: Conn, embedder: Embedder) -> int:
    """每批 commit：全量第一次要很久，中斷後重跑只補沒做完的。"""
    embedded = 0
    after = b""
    while True:
        rows = conn.execute(
            _EMBED_PAGE, {"after": after, "model": embedder.model, "limit": _EMBED_BATCH}
        ).fetchall()
        if not rows:
            return embedded
        vectors = embedder.embed([str(text) for _, text in rows])
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE retrieval.passage SET model = %s, embedding = %s::vector"
                " WHERE content_sha256 = %s",
                [
                    (embedder.model, to_sql(v), digest)
                    for (digest, _), v in zip(rows, vectors, strict=True)
                ],
            )
        conn.commit()
        embedded += len(rows)
        after = cast(bytes, rows[-1][0])
