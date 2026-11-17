"""檢索投影的建置。

投影是可整個重建的衍生物，以內容雜湊為鍵（一列是一段段落文字），不指向版本列。索引單位是段落，
召回單位仍是條，由查詢函式依路徑歸併。兩份衍生物各自成表，因為它們失效的原因不同：

- `retrieval.passage_lexical`：字面索引，依分詞器版本分列（`retrieval.tokenizer`）。分詞器識別碼含
  詞典（所有版本的法規標題）雜湊，新法規出現即變，整個索引都要重算。
- `retrieval.passage`：嵌入向量，只取決於文字與模型，不隨分詞器重算。

分詞器換版採藍綠切換：新版本的索引與詞頻在背景建好，才把 `retrieval.serving` 指過去；建的期間舊版本
照常服務，所以重建不中斷搜尋。查詢端輪詢 `serving`，換了就載入新版本的詞典（`vocabulary` 模組），
不必重啟。舊版本在寬限期（讓每個副本都換過去）之後清掉。

內容定址使索引無從陳舊：

- 文字改了：舊列無人引用（`retrieval.prune` 清掉），新文字沒有列，這裡補上。
- 效力列更替而文字沒變：雜湊相同，沿用原列。

字面臂的高頻詞修剪靠詞頻表（`retrieval.term_stat`）：索引有變動就整批重算，與段落同一個分詞器版本；
沒有統計時查詢不修剪（較慢，不出錯）。
"""

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from canon.db import Conn, fetch, scalar
from canon.embedding import Embeds, to_sql
from canon.exit_codes import JobStopped
from envcite_jobkit import ExitCode, NullMetrics, Policy, RunStats, run
from envcite_segmenter import Segmenter

log = logging.getLogger(__name__)


_PAGE = 2000
#: 一次送給嵌入端點的段落數
_EMBED_BATCH = 32
#: 同時送出的請求數；一個工作項是這麼多批，一起嵌入、一起 commit
_EMBED_WORKERS = 8
#: 一批：（內容雜湊，段落文字）
type Page = list[tuple[bytes, str]]

#: 不再服務的分詞器版本留多久才清：要比查詢端的輪詢間隔（`ServerSettings`）長得多，
#: 讓每個副本都換過去。
GRACE = "10 minutes"
#: 建到一半就放棄的版本（從沒服務過）留多久：要比一次完整建置長，否則別的投影會清掉還在建的。
_ABANDONED = "1 day"

# 一頁要建字面索引的文字：這個分詞器版本還沒有列。以雜湊鍵分頁，現行列雜湊的部分索引可自
# `after` 起依序走（不用一次性的「全部缺列」查詢：逐頁呼叫會變成頁數乘以全表掃描）。
_LEXICAL_PAGE = """
    SELECT DISTINCT ON (v.content_sha256) v.content_sha256, v.content
      FROM corpus.provision_version AS v
      LEFT JOIN retrieval.passage_lexical AS i
             ON i.content_sha256 = v.content_sha256 AND i.tokenizer = %(tokenizer)s
     WHERE v.superseded_at IS NULL
       AND v.content IS NOT NULL
       AND v.content_sha256 > %(after)s
       AND i.content_sha256 IS NULL
     ORDER BY v.content_sha256
     LIMIT %(limit)s
"""

# 一頁要（重新）嵌入的段落：尚未嵌入，或模型不符。
_EMBED_PAGE = """
    SELECT DISTINCT ON (v.content_sha256) v.content_sha256, v.content
      FROM corpus.provision_version AS v
      LEFT JOIN retrieval.passage AS i ON i.content_sha256 = v.content_sha256
     WHERE v.superseded_at IS NULL
       AND v.content IS NOT NULL AND v.content <> ''
       AND v.content_sha256 > %(after)s
       AND i.model IS DISTINCT FROM %(model)s
     ORDER BY v.content_sha256
     LIMIT %(limit)s
"""


class _TitleRow(NamedTuple):
    title: str


class _PassageRow(NamedTuple):
    digest: bytes
    text: str


class EmbeddingStopped(JobStopped, RuntimeError):
    """嵌入沒有走完：政策判定致命，或遇到未分類的錯誤。`code` 是 jobkit 的結束碼。"""

    def __init__(self, code: ExitCode) -> None:
        super().__init__(f"嵌入中止（結束碼 {int(code)}）")
        self.code = code


def rebuild(conn: Conn, embedder: Embeds | None = None, policy: Policy | None = None) -> int:
    """清掉無人引用的列、補齊目前詞典的字面索引並切換服務，有嵌入模型時再補嵌入；回傳新建的字面列數。

    詞典沒變時只補新文字；變了就建出新分詞器版本的整份索引，建好、詞頻算完才切換 `serving`。
    嵌入的重試、退避與斷路依 `policy`（`envcite_jobkit`）。重試用盡的批次跳過，留待下次。
    """
    segmenter = Segmenter(
        [
            row.title
            for row in fetch(
                conn,
                "SELECT DISTINCT title FROM corpus.legal_expression WHERE title IS NOT NULL",
                None,
                _TitleRow,
            )
        ]
    )
    pruned = scalar(conn, "SELECT retrieval.prune()", None, int)
    conn.commit()
    if pruned:
        log.info("清理 %s 列不再被引用的文字的索引", pruned)

    _register(conn, segmenter)
    built = _lexical(conn, segmenter)
    log.info("檢索投影：字面向量新建 %d 列，分詞器 %s", built, segmenter.tokenizer_id)
    _term_stat(conn, segmenter.tokenizer_id, changed=bool(built or pruned))
    _serve(conn, segmenter.tokenizer_id)
    _retire(conn)
    if embedder is not None:
        embedded = _embed(conn, embedder, policy=policy)
        log.info("檢索投影：嵌入 %d 段，模型 %s", embedded, embedder.model)
    return built


def _register(conn: Conn, segmenter: Segmenter) -> None:
    """登記這個分詞器版本與它的詞典；查詢端要靠詞典才切得出同樣的詞。已登記的不動。"""
    conn.execute(
        "INSERT INTO retrieval.tokenizer (id, terms) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (segmenter.tokenizer_id, list(segmenter.terms)),
    )
    conn.commit()


def _serve(conn: Conn, tokenizer: str) -> None:
    """把服務切到這個版本（索引與詞頻都已建完）；原本服務的版本記下退場時間，寬限期後清。"""
    previous = conn.execute("SELECT tokenizer FROM retrieval.serving").fetchone()
    if previous is not None and previous[0] == tokenizer:
        return
    with conn.transaction():
        conn.execute(
            "UPDATE retrieval.tokenizer SET retired_at = now()"
            " WHERE id = (SELECT tokenizer FROM retrieval.serving) AND retired_at IS NULL"
        )
        conn.execute("UPDATE retrieval.tokenizer SET retired_at = NULL WHERE id = %s", (tokenizer,))
        conn.execute(
            "INSERT INTO retrieval.serving (tokenizer) VALUES (%s)"
            " ON CONFLICT (singleton) DO UPDATE SET tokenizer = EXCLUDED.tokenizer",
            (tokenizer,),
        )
    log.info("檢索投影：服務切換到分詞器 %s", tokenizer)


def _retire(conn: Conn) -> None:
    """清掉已退場超過寬限期、或建到一半就放棄的分詞器版本（索引與詞頻隨外鍵連鎖刪除）。"""
    gone = conn.execute(
        "DELETE FROM retrieval.tokenizer"
        " WHERE id <> (SELECT tokenizer FROM retrieval.serving)"
        "   AND ((retired_at IS NOT NULL AND retired_at < now() - %s::interval)"
        "     OR (retired_at IS NULL AND created_at < now() - %s::interval))",
        (GRACE, _ABANDONED),
    ).rowcount
    conn.commit()
    if gone:
        log.info("檢索投影：清掉 %d 個不再服務的分詞器版本", gone)


def _term_stat(conn: Conn, tokenizer: str, *, changed: bool) -> None:
    """索引有變動、或這個分詞器還沒有統計時，整批重算詞頻；其餘情形統計仍然有效。"""
    current = conn.execute(
        "SELECT 1 FROM retrieval.term_stat_total WHERE tokenizer = %s", (tokenizer,)
    ).fetchone()
    if current and not changed:
        return
    terms = scalar(conn, "SELECT retrieval.refresh_term_stat(%s)", (tokenizer,), int)
    conn.commit()
    log.info("檢索投影：詞頻 %s 個詞", terms)


def _lexical(conn: Conn, segmenter: Segmenter) -> int:
    tokenizer = segmenter.tokenizer_id
    built = 0
    after = b""
    while True:
        rows = fetch(
            conn,
            _LEXICAL_PAGE,
            {"after": after, "tokenizer": tokenizer, "limit": _PAGE},
            _PassageRow,
        )
        if not rows:
            return built
        batch = [
            (row.digest, tokenizer, segmenter.to_tsvector_input(row.text))
            for row in rows
            if row.text
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO retrieval.passage_lexical (content_sha256, tokenizer, tsv)"
                " VALUES (%s, %s, to_tsvector('simple', %s))"
                " ON CONFLICT (content_sha256, tokenizer) DO NOTHING",
                batch,
            )
        conn.commit()
        built += len(batch)
        after = rows[-1].digest


def _embed(conn: Conn, embedder: Embeds, *, policy: Policy | None = None) -> int:
    """每頁 commit：全量第一次要很久，中斷後重跑只補沒做完的。

    一頁（`_EMBED_WORKERS` 批）是 jobkit 的一個工作項，批次並行送出以壓低全量的牆鐘時間，
    資料庫只由本執行緒寫。端點問題退避重試並計入斷路器（重試整頁），重試用盡或內容被拒的頁
    跳過（其列仍未嵌入，下次重跑會再選到）。
    """
    stats = RunStats()
    embedded = 0

    def pages() -> Iterator[Page]:
        after = b""
        while True:
            rows = fetch(
                conn,
                _EMBED_PAGE,
                {
                    "after": after,
                    "model": embedder.model,
                    "limit": _EMBED_BATCH * _EMBED_WORKERS,
                },
                _PassageRow,
            )
            if not rows:
                return
            yield [(row.digest, row.text) for row in rows]
            after = rows[-1].digest

    def embed_page(rows: Page) -> None:
        nonlocal embedded
        chunks = [rows[i : i + _EMBED_BATCH] for i in range(0, len(rows), _EMBED_BATCH)]
        with ThreadPoolExecutor(len(chunks)) as pool:
            results = pool.map(lambda c: embedder.embed([text for _, text in c]), chunks)
            vectors = [v for chunk in results for v in chunk]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO retrieval.passage (content_sha256, model, embedding)"
                " VALUES (%s, %s, %s::vector)"
                " ON CONFLICT (content_sha256)"
                " DO UPDATE SET model = EXCLUDED.model, embedding = EXCLUDED.embedding",
                [
                    (digest, embedder.model, to_sql(v))
                    for (digest, _), v in zip(rows, vectors, strict=True)
                ],
            )
        conn.commit()
        embedded += len(rows)
        if embedded % (_EMBED_BATCH * _EMBED_WORKERS * 10) < len(rows):
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
