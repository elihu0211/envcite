"""檢索投影的建置。

`retrieval.passage` 是可整個重建的衍生物，且為內容定址：一列是一段段落文字（以雜湊為鍵）的
字面向量與嵌入向量，各記產生它的分詞器與模型，不指向版本列。索引單位是段落，召回單位仍是條，
由查詢函式依路徑歸併。

內容定址使索引無從陳舊：

- 文字改了：舊列無人引用（`retrieval.prune` 清掉），新文字沒有列，這裡補上。
- 效力列更替而文字沒變：雜湊相同，沿用原列。

字面臂的高頻詞修剪靠詞頻表（`retrieval.term_stat`）：索引有變動就整批重算，與段落同一個
分詞器識別碼；沒有統計時查詢不修剪（較慢，不出錯）。

分詞器識別碼含詞典（法規名稱）雜湊，新法規出現即變：不符的列就地重算字面向量，嵌入不動。
模型換了同理，只重算嵌入。服務端詞典啟動時讀一次，重建後須重啟才採用新識別碼。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import cast

import psycopg

from envcite_jobkit import ExitCode, NullMetrics, Policy, RunStats, run
from envcite_ontology.embedding import Embedder, to_sql
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

_PAGE = 2000
#: 一次送給嵌入端點的段落數
_EMBED_BATCH = 32
#: 一批：（內容雜湊，段落文字）
Page = list[tuple[bytes, str]]

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
       AND (%(pcodes)s::text[] IS NULL OR v.resource_id IN (
            SELECT r.id FROM corpus.legal_resource AS r
             WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(%(pcodes)s)))
     ORDER BY i.content_sha256
     LIMIT %(limit)s
"""


class EmbeddingStopped(RuntimeError):
    """嵌入沒有走完：政策判定致命，或遇到未分類的錯誤。`code` 是 jobkit 的結束碼。"""

    def __init__(self, code: ExitCode) -> None:
        super().__init__(f"嵌入中止（結束碼 {int(code)}）")
        self.code = int(code)


def rebuild(conn: Conn, embedder: Embedder | None = None, policy: Policy | None = None) -> int:
    """清掉無人引用的列、補齊字面向量，有嵌入模型時再補嵌入；回傳新建或重算的字面列數。

    嵌入的重試、退避與斷路依 `policy`（`envcite_jobkit`）。重試用盡的批次跳過，留待下次。
    """
    segmenter = Segmenter(
        [str(t) for (t,) in conn.execute("SELECT DISTINCT title FROM corpus.legal_expression")]
    )
    pruned = conn.execute("SELECT retrieval.prune()").fetchone()
    conn.commit()
    if pruned and pruned[0]:
        log.info("清理 %s 列不再被引用的文字的索引", pruned[0])

    built = _lexical(conn, segmenter)
    log.info("檢索投影：字面向量新建或重算 %d 列，分詞器 %s", built, segmenter.tokenizer_id)
    _term_stat(conn, segmenter.tokenizer_id, changed=bool(built or (pruned and pruned[0])))
    if embedder is not None:
        embedded = _embed(conn, embedder, policy=policy)
        log.info("檢索投影：嵌入 %d 段，模型 %s", embedded, embedder.model)
    return built


def _term_stat(conn: Conn, tokenizer: str, *, changed: bool) -> None:
    """索引有變動、或這個分詞器還沒有統計時，整批重算詞頻；其餘情形統計仍然有效。"""
    current = conn.execute(
        "SELECT 1 FROM retrieval.term_stat_total WHERE tokenizer = %s", (tokenizer,)
    ).fetchone()
    if current and not changed:
        return
    row = conn.execute("SELECT retrieval.refresh_term_stat(%s)", (tokenizer,)).fetchone()
    conn.commit()
    log.info("檢索投影：詞頻 %s 個詞", row[0] if row else 0)


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


def embed_scope(
    conn: Conn, embedder: Embedder, pcodes: Sequence[str], policy: Policy | None = None
) -> int:
    """只為這些法規（moj_pcode）現行版本的段落補嵌入，其餘不動；其餘同 `rebuild` 的嵌入步驟。

    供範圍限定的量測用；字面向量須已建好（列要先在）。同一段文字也出現在範圍外的法規時，
    它的列只有一列，嵌入隨之而有。
    """
    return _embed(conn, embedder, pcodes=list(pcodes), policy=policy)


def _embed(
    conn: Conn,
    embedder: Embedder,
    *,
    pcodes: list[str] | None = None,
    policy: Policy | None = None,
) -> int:
    """每批 commit：全量第一次要很久，中斷後重跑只補沒做完的。`pcodes` 為空值時不限範圍。

    一批是 jobkit 的一個工作項：端點問題退避重試並計入斷路器，重試用盡或內容被拒的批次
    跳過（其列仍未嵌入，下次重跑會再選到）。
    """
    stats = RunStats()
    embedded = 0

    def pages() -> Iterator[Page]:
        after = b""
        while True:
            rows = conn.execute(
                _EMBED_PAGE,
                {"after": after, "model": embedder.model, "limit": _EMBED_BATCH, "pcodes": pcodes},
            ).fetchall()
            if not rows:
                return
            yield [(cast(bytes, digest), str(text)) for digest, text in rows]
            after = cast(bytes, rows[-1][0])

    def embed_page(rows: Page) -> None:
        nonlocal embedded
        vectors = embedder.embed([text for _, text in rows])
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
        if embedded % (_EMBED_BATCH * 50) < len(rows):
            log.info("檢索投影：已嵌入 %d 段", embedded)

    code = run(
        pages(),
        embed_page,
        policy,
        item_id=lambda rows: rows[0][0].hex(),
        metrics=NullMetrics(),
        stats=stats,
    )
    if stats.dead_lettered:
        log.warning("檢索投影：%d 批嵌入失敗而跳過，下次同步再補", stats.dead_lettered)
    if code is not ExitCode.OK:
        raise EmbeddingStopped(code)
    return embedded
