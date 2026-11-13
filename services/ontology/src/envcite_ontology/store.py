"""法規語料（PostgreSQL）之上的唯讀模型；資料表是唯一真值。

版本解析、閱讀順序、IRI、條文全文都由 SQL 決定，本模組不重算，以免兩處答案分岔。
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from types import TracebackType
from typing import Any, Self

import psycopg
from psycopg_pool import ConnectionPool

from envcite_ontology.embedding import to_sql
from envcite_ontology.law_names import LawNames

#: 語料目前只有中文版本。
LANGUAGE = "zho"


Conn = psycopg.Connection[tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class LawKey:
    """法規的自然鍵（管轄，識別碼方案，在地識別碼）；三個欄位都由呼叫端明說，沒有預設。

    管轄與方案是否配對由資料庫強制（`legal_resource_scheme_matches_jurisdiction`）。
    """

    jurisdiction: str
    id_scheme: str
    local_id: str

    def __str__(self) -> str:
        return f"{self.jurisdiction}:{self.id_scheme}:{self.local_id}"


@dataclass(frozen=True, slots=True)
class LawRecord:
    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    language: str
    date_document: str


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    iri: str
    number: str


@dataclass(frozen=True, slots=True)
class InvalidationRecord:
    ruling_no: str
    #: `corpus.ruling_kind` 的值：interpretation 或 judgment
    ruling_kind: str
    decided_on: str
    source_url: str
    excerpt: str
    path: str
    invalid_from: str
    partial: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    iri: str
    jurisdiction: str
    id_scheme: str
    local_id: str
    law_title: str
    number: str
    content: str
    lexical_score: float
    semantic_similarity: float | None = None


def is_valid_law_id(value: str) -> bool:
    return bool(value and len(value) <= 64)


#: 某日有效、有全文的版本；法規不存在、該日無版本或該日的版本查不到全文（`text_status`）皆查無結果。
_RESOLVE = """
    SELECT r.id, corpus.resource_iri(r.norm_kind, r.local_id), e.title, e.language,
           (SELECT min(le.event_date)
              FROM corpus.lifecycle_event AS le
             WHERE le.resource_id = r.id AND le.event_kind = 'enactment'),
           r.jurisdiction, r.id_scheme, r.local_id
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
     WHERE r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction
       AND r.id_scheme = %(id_scheme)s::corpus.id_scheme
       AND r.local_id = %(law)s
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

#: 該日有效的條，含 `text_unavailable`（有效，只是無文字）。
_LIST_ARTICLES = """
    SELECT corpus.version_iri(s.version_id), corpus.article_number(s.path)
      FROM corpus.provisions_at(%(resource)s, %(language)s, %(on)s::date) AS s
     WHERE s.provision_type = 'article'
       AND s.status IN ('in_force', 'text_unavailable')
     ORDER BY s.sort_key
"""

_JURISDICTIONS = "SELECT unnest(enum_range(NULL::corpus.jurisdiction))::text"
_ID_SCHEMES = "SELECT unnest(enum_range(NULL::corpus.id_scheme))::text"

#: segmenter 詞典（法規名稱）；須與建索引同源，否則分詞器識別碼不符、字面檢索全落空（04-01 §5）。
_LEXICON = "SELECT DISTINCT title FROM corpus.legal_expression WHERE title IS NOT NULL"

#: 標題（含各版本的舊名稱）→ 法規代碼；問句點名法規用（`law_names.LawNames`）。
_LAW_TITLES = """
    SELECT e.title, array_agg(DISTINCT r.local_id)
      FROM corpus.legal_expression AS e
      JOIN corpus.legal_resource AS r ON r.id = e.resource_id
     WHERE e.title IS NOT NULL
     GROUP BY e.title
"""

_SEARCH = """
    SELECT corpus.version_iri(s.version_id), r.jurisdiction, r.id_scheme, r.local_id, e.title,
           corpus.article_number(s.path), corpus.article_text(s.version_id),
           s.lexical_score, s.semantic_similarity
      FROM retrieval.search_expanded(
               retrieval.prune_terms(%(terms)s, %(tokenizer)s, %(max_df)s::double precision),
               %(tokenizer)s, %(embedding)s::vector, %(model)s,
               %(on)s::date, %(language)s, %(limit)s, %(pool)s,
               CASE WHEN cardinality(%(laws)s::text[]) = 0 THEN NULL
                    ELSE retrieval.law_scope(%(laws)s::text[], %(language)s, %(on)s::date)
               END
           ) AS s
      JOIN corpus.legal_resource AS r ON r.id = s.resource_id
      JOIN corpus.provision_version AS v ON v.id = s.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     WHERE (%(jurisdiction)s::corpus.jurisdiction IS NULL
            OR r.jurisdiction = %(jurisdiction)s::corpus.jurisdiction)
     ORDER BY s.rank
"""

#: 每一臂的候選池（條數）。融合前各取這麼多；尚未以題庫量測，取 limit 上限的數倍。
SEARCH_POOL = 50


class OntologyStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> Self:
        return cls(ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True))

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def get_law(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> LawRecord | None:
        """`as_of`（ISO 日期，空為今日）有效版本的法規；法規不存在或該日無版本時為 None。"""
        found = self._resolve(key, as_of, timeout=timeout)
        return found[1] if found else None

    def list_articles(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[ArticleRecord] | None:
        """`as_of` 當日有效的各條；法規或版本不存在為 None，有版本而無有效條為 []，兩者語意不同。"""
        deadline = _deadline(timeout)
        found = self._resolve(key, as_of, timeout=_remaining(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            cur.execute(
                _LIST_ARTICLES,
                {"resource": resource_id, "language": LANGUAGE, "on": _date(as_of)},
            )
            return [ArticleRecord(iri=iri, number=number) for iri, number in cur.fetchall()]

    def list_invalidations(
        self,
        key: LawKey,
        *,
        as_of: str = "",
        article_number: str = "",
        timeout: float | None = None,
    ) -> list[InvalidationRecord] | None:
        """`as_of` 當日因違憲而失效的條文單元；法規或版本不存在為 None，沒有失效為 []。

        `article_number` 非空時只看該條（含其下各項）。
        """
        deadline = _deadline(timeout)
        found = self._resolve(key, as_of, timeout=_remaining(deadline))
        if found is None:
            return None
        resource_id, _ = found
        with self.read_connection(timeout=_remaining(deadline)) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT i.ruling_no, r.ruling_kind::text, r.decided_on, r.source_url, r.excerpt,"
                "       i.path, i.invalid_from, x.partial"
                "  FROM corpus.invalidations_at(%(resource)s, %(language)s, %(on)s::date) AS i"
                "  JOIN corpus.constitutional_ruling AS r ON r.id = i.ruling_id"
                "  JOIN corpus.ruling_invalidation AS x"
                "    ON x.ruling_id = i.ruling_id AND x.provision_id = i.provision_id"
                " WHERE %(article)s = '' OR i.path = 'art_' || %(article)s"
                "       OR starts_with(i.path, 'art_' || %(article)s || '/')"
                " ORDER BY r.decided_on, i.ruling_id, i.provision_id",
                {
                    "resource": resource_id,
                    "language": LANGUAGE,
                    "on": _date(as_of),
                    "article": article_number.strip(),
                },
            )
            return [
                InvalidationRecord(
                    no, kind, decided.isoformat(), url, excerpt, path, since.isoformat(), partial
                )
                for no, kind, decided, url, excerpt, path, since, partial in cur.fetchall()
            ]

    def law_exists(self, key: LawKey, *, timeout: float | None = None) -> bool:
        """法規在語料裡有沒有，不論哪一天。區分「查無此法」與「該日無版本」。"""
        if not is_valid_law_id(key.local_id):
            return False
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM corpus.legal_resource"
                " WHERE jurisdiction = %s::corpus.jurisdiction"
                "   AND id_scheme = %s::corpus.id_scheme"
                "   AND local_id = %s",
                (key.jurisdiction, key.id_scheme, key.local_id),
            )
            return cur.fetchone() is not None

    def resource_id(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> int | None:
        """`as_of` 有效版本的法規之內部編號；法規不存在或該日無版本時為 None。"""
        found = self._resolve(key, as_of, timeout=timeout)
        return found[0] if found else None

    @cached_property
    def jurisdictions(self) -> frozenset[str]:
        """綱要接受的管轄（`corpus.jurisdiction`）；封閉集合，啟動後第一次用到時讀一次。"""
        return self._values(_JURISDICTIONS)

    @cached_property
    def id_schemes(self) -> frozenset[str]:
        """綱要接受的識別碼方案（`corpus.id_scheme`）；讀取時機同 `jurisdictions`。"""
        return self._values(_ID_SCHEMES)

    def _values(self, query: str) -> frozenset[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query)
            return frozenset(str(value) for (value,) in cur.fetchall())

    @contextmanager
    def read_connection(
        self, *, timeout: float | None = None
    ) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        """唯讀連線，在一個交易內（結束時一律回滾）。

        `timeout`（秒）同時是跟池子等待的上限與這個交易的 `statement_timeout`：呼叫的期限用完，
        查詢被取消，也不在池裡等到呼叫端已經離開。沒有期限時沿用池子自己的等待上限，不設
        `statement_timeout`。

        期限已經用完時直接丟 `QueryCanceled`，不向池子要連線：等待時間不大於 0 時，psycopg
        即使池裡有空連線也丟 `PoolTimeout`，那會被誤記成容量用盡。列保持未定型，查詢在讀到的
        地方直接組成紀錄；收成 `object` 會讓每一欄都要再轉一次。
        """
        if timeout is not None and timeout <= 0:
            raise psycopg.errors.QueryCanceled("deadline already spent")
        with self._pool.connection(timeout=timeout) as conn, conn.transaction(force_rollback=True):
            if timeout is not None:
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(max(1, int(timeout * 1000))),),
                )
            yield conn

    def lexicon(self) -> list[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LEXICON)
            return [title for (title,) in cur.fetchall()]

    def law_names(self) -> LawNames:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LAW_TITLES)
            return LawNames({title: pcodes for title, pcodes in cur.fetchall()})

    def searched_terms(
        self, terms: str, *, tokenizer: str, max_df: float = 0.0, timeout: float | None = None
    ) -> list[str]:
        """字面臂實際查的詞：`search` 以同樣的 `max_df` 略過高頻詞之後留下的詞。"""
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT retrieval.prune_terms("
                "%(terms)s, %(tokenizer)s, %(max_df)s::double precision)",
                {"terms": terms, "tokenizer": tokenizer, "max_df": max_df},
            )
            row = cur.fetchone()
        return str(row[0] if row else "").split()

    def search(
        self,
        terms: str,
        *,
        tokenizer: str,
        as_of: str = "",
        limit: int = 10,
        embedding: Sequence[float] | None = None,
        model: str = "",
        max_df: float = 0.0,
        laws: Sequence[str] = (),
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        """`as_of`（空為今日）有效的條，字面臂比對已分詞的 `terms`，語意臂比對 `embedding`。

        沒有 `embedding` 時只用字面臂。兩臂以名次倒數融合排序（`retrieval.search_hybrid`），每條之後
        緊接處罰它（或授權其所在命令的條）的罰則條，分數取帶出它的那一條（`retrieval.search_expanded`）。
        `max_df`（段落比例）大於 0 時，字面臂先略過出現在超過這個比例的段落的詞
        （`retrieval.prune_terms`）；0 為不修剪。

        `laws`（法規代碼）非空時兩臂只在這些法規與依其授權訂定的命令裡找
        （`retrieval.law_scope`）；範圍在 `as_of` 沒有任何可檢索的條時，改在全庫找，不回空。

        `timeout`（秒）是這一次檢索剩下的預算，兩次查詢共用；沒有期限時為 None。
        """
        deadline = _deadline(timeout)
        hits = self._search(
            terms,
            tokenizer,
            as_of,
            limit,
            embedding,
            model,
            max_df,
            tuple(laws),
            jurisdiction,
            timeout=_remaining(deadline),
        )
        if laws and not hits:
            hits = self._search(
                terms,
                tokenizer,
                as_of,
                limit,
                embedding,
                model,
                max_df,
                (),
                jurisdiction,
                timeout=_remaining(deadline),
            )
        return hits

    def _search(
        self,
        terms: str,
        tokenizer: str,
        as_of: str,
        limit: int,
        embedding: Sequence[float] | None,
        model: str,
        max_df: float,
        laws: tuple[str, ...],
        jurisdiction: str | None = None,
        timeout: float | None = None,
    ) -> list[SearchHit]:
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _SEARCH,
                {
                    "laws": list(laws),
                    "terms": terms,
                    "tokenizer": tokenizer,
                    "max_df": max_df,
                    "on": _date(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                    "embedding": None if embedding is None else to_sql(embedding),
                    "model": model,
                    "pool": SEARCH_POOL,
                    "jurisdiction": jurisdiction,
                },
            )
            return [
                SearchHit(
                    iri=iri,
                    jurisdiction=jur,
                    id_scheme=scheme,
                    local_id=lid,
                    law_title=title,
                    number=number,
                    content=content,
                    lexical_score=lexical,
                    semantic_similarity=semantic,
                )
                for (
                    iri,
                    jur,
                    scheme,
                    lid,
                    title,
                    number,
                    content,
                    lexical,
                    semantic,
                ) in cur.fetchall()
            ]

    def _resolve(
        self, key: LawKey, as_of: str, *, timeout: float | None = None
    ) -> tuple[int, LawRecord] | None:
        if not is_valid_law_id(key.local_id):
            return None
        with self.read_connection(timeout=timeout) as conn, conn.cursor() as cur:
            cur.execute(
                _RESOLVE,
                {
                    "law": key.local_id,
                    "language": LANGUAGE,
                    "on": _date(as_of),
                    "jurisdiction": key.jurisdiction,
                    "id_scheme": key.id_scheme,
                },
            )
            row = cur.fetchone()
        if row is None:
            return None
        (
            resource_id,
            iri,
            title,
            language,
            enacted,
            jur,
            scheme,
            lid,
        ) = row
        return resource_id, LawRecord(
            jurisdiction=jur,
            id_scheme=scheme,
            local_id=lid,
            iri=iri,
            title=title,
            language=language,
            date_document=enacted.isoformat() if enacted is not None else "",
        )


def _date(as_of: str) -> str:
    """空 `as_of` 為今日，由資料庫解析以與查詢同一時鐘。"""
    return as_of or "today"


def _deadline(timeout: float | None) -> float | None:
    """`timeout` 秒後的 monotonic 時刻；沒有期限為 None。"""
    if timeout is None:
        return None
    return time.monotonic() + timeout


def _remaining(deadline: float | None) -> float | None:
    """距離 `deadline` 還剩的秒數；沒有期限為 None。"""
    if deadline is None:
        return None
    return deadline - time.monotonic()
