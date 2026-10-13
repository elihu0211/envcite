"""法規語料（PostgreSQL）之上的唯讀模型；資料表為真值，本體是投影（env-law-docs 02-02）。

版本解析、閱讀順序、IRI、條文全文都由 SQL 決定，本模組不重算，以免兩處答案分岔。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import psycopg
from psycopg_pool import ConnectionPool

#: 全國法規資料庫 pcode 的形狀，對應 `corpus.legal_resource` 的 CHECK；只省一次往返，
#: 不是注入防禦（查詢皆已參數化）。
_PCODE = re.compile(r"^[A-Z][0-9]{7}$")

#: 語料目前只有中文版本。
LANGUAGE = "zho"


Conn = psycopg.Connection[tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class LawRecord:
    id: str
    iri: str
    title: str
    language: str
    #: 制定日（eli:date_document），ISO 8601；無制定事件時為空字串。
    date_document: str


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    iri: str
    number: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    iri: str
    law_id: str
    law_title: str
    number: str
    content: str
    lexical_score: float


def is_valid_law_id(value: str) -> bool:
    return bool(_PCODE.fullmatch(value))


#: 某日有效的版本；法規不存在與該日無版本皆查無結果。
_RESOLVE = """
    SELECT r.id, corpus.resource_iri(r.norm_kind, r.local_id), e.title, e.language,
           (SELECT min(le.event_date)
              FROM corpus.lifecycle_event AS le
             WHERE le.resource_id = r.id AND le.event_kind = 'enactment')
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
     WHERE r.id_scheme = 'moj_pcode'
       AND r.local_id = %(law)s
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
"""

#: 該日有效的條，含 `text_unavailable`（有效，只是無文字）。
_LIST_ARTICLES = """
    SELECT corpus.version_iri(s.version_id), corpus.article_number(s.path)
      FROM corpus.provisions_at(%(resource)s, %(language)s, %(on)s::date) AS s
     WHERE s.provision_type = 'article'
       AND s.status IN ('in_force', 'text_unavailable')
     ORDER BY s.sort_key
"""

#: segmenter 詞典（法規名稱）；須與建索引同源，否則分詞器識別碼不符、字面檢索全落空（04-01 §5）。
_LEXICON = "SELECT DISTINCT title FROM corpus.legal_expression"

_SEARCH = """
    SELECT corpus.version_iri(s.version_id), r.local_id, e.title,
           corpus.article_number(s.path), corpus.article_text(s.version_id),
           s.lexical_score
      FROM retrieval.search_articles(
               %(terms)s, %(tokenizer)s, %(on)s::date, %(language)s, %(limit)s
           ) AS s
      JOIN corpus.legal_resource AS r ON r.id = s.resource_id
      JOIN corpus.provision_version AS v ON v.id = s.version_id
      JOIN corpus.legal_expression AS e ON e.id = v.expression_id
     ORDER BY s.lexical_score DESC, s.version_id
"""


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

    def get_law(self, law_id: str, *, as_of: str = "") -> LawRecord | None:
        """`as_of`（ISO 日期，空為今日）有效版本的法規；法規不存在或該日無版本時為 None。"""
        found = self._resolve(law_id, as_of)
        return found[1] if found else None

    def list_articles(self, law_id: str, *, as_of: str = "") -> list[ArticleRecord] | None:
        """`as_of` 當日有效的各條；法規或版本不存在為 None，有版本而無有效條為 []，兩者語意不同。"""
        found = self._resolve(law_id, as_of)
        if found is None:
            return None
        resource_id, _ = found
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _LIST_ARTICLES,
                {"resource": resource_id, "language": LANGUAGE, "on": _date(as_of)},
            )
            return [ArticleRecord(iri=iri, number=number) for iri, number in cur.fetchall()]

    def law_exists(self, law_id: str) -> bool:
        """法規在語料裡有沒有，不論哪一天。區分「查無此法」與「該日無版本」。"""
        if not is_valid_law_id(law_id):
            return False
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM corpus.legal_resource"
                " WHERE id_scheme = 'moj_pcode' AND local_id = %s",
                (law_id,),
            )
            return cur.fetchone() is not None

    def resource_id(self, law_id: str, *, as_of: str = "") -> int | None:
        """`as_of` 有效版本的法規之內部編號；法規不存在或該日無版本時為 None。"""
        found = self._resolve(law_id, as_of)
        return found[0] if found else None

    @contextmanager
    def read_connection(self, *, timeout: float | None = None) -> Iterator[Conn]:
        """唯讀連線，在一個交易內（結束時一律回滾）。

        `timeout`（秒）成為這個交易的 `statement_timeout`：呼叫的期限用完，資料庫端的查詢跟著被
        取消，不佔著連線跑完。
        """
        with self._pool.connection() as conn, conn.transaction(force_rollback=True):
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

    def search(
        self, terms: str, *, tokenizer: str, as_of: str = "", limit: int = 10
    ) -> list[SearchHit]:
        """`as_of`（空為今日）有效、與已分詞之 `terms` 相符的條。"""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _SEARCH,
                {
                    "terms": terms,
                    "tokenizer": tokenizer,
                    "on": _date(as_of),
                    "language": LANGUAGE,
                    "limit": limit,
                },
            )
            return [
                SearchHit(
                    iri=iri,
                    law_id=law_id,
                    law_title=title,
                    number=number,
                    content=content,
                    lexical_score=lexical,
                )
                for iri, law_id, title, number, content, lexical in cur.fetchall()
            ]

    def _resolve(self, law_id: str, as_of: str) -> tuple[int, LawRecord] | None:
        if not is_valid_law_id(law_id):
            return None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_RESOLVE, {"law": law_id, "language": LANGUAGE, "on": _date(as_of)})
            row = cur.fetchone()
        if row is None:
            return None
        resource_id, iri, title, language, enacted = row
        return resource_id, LawRecord(
            id=law_id,
            iri=iri,
            title=title,
            language=language,
            date_document=enacted.isoformat() if enacted is not None else "",
        )


def _date(as_of: str) -> str:
    """空 `as_of` 為今日，由資料庫解析以與查詢同一時鐘。"""
    return as_of or "today"
