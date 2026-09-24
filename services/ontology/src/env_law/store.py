"""ELI 法規資料（PostgreSQL）之上的讀取模型。

關聯式資料表才是真值；本體是由它們投影出來的視圖。這個方向不得反轉——見
env-law-docs 02-02。本模組只讀；寫入屬於匯入管線，而該管線尚未存在。

排序不在 Python 裡做。閱讀順序（「5-1」在「5」之後、「6」之前）由衍生欄位
`subdivision.sort_key` 承載，查詢只需依它 ORDER BY。排序是資料的性質，不是呼叫端
的偏好：交給呼叫端決定，遲早會有第二個呼叫端排出不同的順序。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Self

from psycopg_pool import ConnectionPool

#: local id 的形狀。與 `legal_resource.id_local` 上的 CHECK 相互對應；兩者都需要。
#: 資料庫那一道守住寫入，這一道則讓不可能相符的值省去一次往返。它不是注入防禦——
#: 查詢都已參數化——而是一道廉價的拒絕。
_LOCAL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


class VersionStatus(Enum):
    """某時點的版本解析為何產出（或未產出）條文。"""

    RESOLVED = "resolved"
    #: 語料中沒有這部法規。
    UNKNOWN_LAW = "unknown_law"
    #: 法規存在，但持有的版本中沒有任何一個涵蓋所請求的日期。
    NO_VERSION = "no_version"


@dataclass(frozen=True, slots=True)
class LawRecord:
    id: str
    iri: str
    title: str
    language: str
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
    #: 融合後的排序分數：只用來排序命中結果，不表示相關性（見 0010）。
    rank_score: float
    lexical_score: float | None
    dense_distance: float | None


def is_valid_local_id(value: str) -> bool:
    return bool(_LOCAL_ID.fullmatch(value))


#: 某日期施行中的條文；當 `p_on` 為 NULL 時，則為沒有終止日的那一版。
#: 解析在 SQL 裡進行，讓「哪一個版本」只有唯一答案；若兩個呼叫端各自在 Python 裡
#: 挑版本，遲早會挑得不一樣。
_RESOLVE_EXPRESSION = """
    SELECT e.iri, e.title, e.language
      FROM legal_expression AS e
     WHERE e.resource_id = %(law)s
       AND (
             (%(on)s::date IS NULL AND e.valid_to IS NULL)
          OR (%(on)s::date IS NOT NULL
              AND daterange(e.valid_from, e.valid_to, '[)') @> %(on)s::date)
           )
     LIMIT 1
"""

#: segmenter 的 lexicon：法定用語與法規名稱（env-law-docs 04-01 §4）。
#: 匯入程式必須用同一個查詢建立 `content_tsv`——lexicon 不同就會得到不同的
#: `tokenizer` id，此時字面檢索臂會什麼都比對不到，而不是比對到錯的東西。
_LEXICON = """
    SELECT term FROM term_definition
    UNION
    SELECT title FROM legal_expression
"""

#: 在選定並量測 embedding 模型之前，稠密檢索臂維持關閉（向量與模型皆為 NULL）；
#: 此時 `search_articles` 只回傳字面檢索的命中結果。
_SEARCH = """
    SELECT s.iri, s.resource_id, e.title, s.number, s.content,
           s.score, s.lexical_score, s.dense_distance
      FROM search_articles(
               %(terms)s, NULL, NULL, %(tokenizer)s,
               coalesce(%(on)s::date, current_date),
               p_limit => %(limit)s
           ) AS s
      JOIN subdivision AS d ON d.iri = s.iri
      JOIN legal_expression AS e ON e.iri = d.expression_iri
     ORDER BY s.score DESC, s.iri
"""

_GET_RESOURCE = "SELECT iri, date_document FROM legal_resource WHERE id_local = %s"

_LIST_ARTICLES = """
    SELECT s.iri, s.number
      FROM subdivision AS s
     WHERE s.expression_iri = %s
     ORDER BY s.sort_key
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
        """`as_of`（ISO 日期）當時施行的法規；為空時取現行版本。

        法規不存在與沒有版本涵蓋該日期，兩種情況都回傳 None。這是兩個不同的事實，
        由 `version_status` 加以區分——本方法是給不在意差別的呼叫端的便捷途徑。
        """
        status, record = self.resolve(law_id, as_of=as_of)
        return record if status is VersionStatus.RESOLVED else None

    def resolve(self, law_id: str, *, as_of: str = "") -> tuple[VersionStatus, LawRecord | None]:
        """解析法規在某時點的版本，並區分兩種失敗方式。

        要緊的是 `NO_VERSION`：法規存在，但語料並未持有該日期施行的條文。若以現行
        條文作答，將與正確答案無從區分，因此呼叫端必須能夠表明自己不知道。
        """
        if not is_valid_local_id(law_id):
            return VersionStatus.UNKNOWN_LAW, None
        on = as_of or None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_GET_RESOURCE, (law_id,))
            resource = cur.fetchone()
            if resource is None:
                return VersionStatus.UNKNOWN_LAW, None
            iri, date_document = resource

            cur.execute(_RESOLVE_EXPRESSION, {"law": law_id, "on": on})
            expression = cur.fetchone()
        if expression is None:
            return VersionStatus.NO_VERSION, None
        _, title, language = expression
        return VersionStatus.RESOLVED, LawRecord(
            id=law_id,
            iri=iri,
            title=title,
            language=language,
            # 未知的日期在資料庫中為 NULL；線上契約的型別是字串，因此「未知」
            # 以空字串表示。
            date_document=date_document.isoformat() if date_document is not None else "",
        )

    def list_articles(self, law_id: str, *, as_of: str = "") -> list[ArticleRecord] | None:
        """`as_of` 當時施行條文的各條。

        法規不存在或沒有版本涵蓋該日期時為 None；該版本確實沒有任何條文時為 []。
        兩者必須保持可區分：前者表示語料缺漏，後者是關於該法規的事實。
        """
        if not is_valid_local_id(law_id):
            return None
        on = as_of or None
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_RESOLVE_EXPRESSION, {"law": law_id, "on": on})
            expression = cur.fetchone()
            if expression is None:
                return None
            cur.execute(_LIST_ARTICLES, (expression[0],))
            return [ArticleRecord(iri=iri, number=number) for iri, number in cur.fetchall()]

    def lexicon(self) -> list[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_LEXICON)
            return [term for (term,) in cur.fetchall()]

    def search(
        self, terms: str, *, tokenizer: str, as_of: str = "", limit: int = 10
    ) -> list[SearchHit]:
        """`as_of`（為空時為今日）施行中、與已斷詞之 `terms` 相符的條文。"""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                _SEARCH,
                {"terms": terms, "tokenizer": tokenizer, "on": as_of or None, "limit": limit},
            )
            return [
                SearchHit(
                    iri=iri,
                    law_id=law_id,
                    law_title=title,
                    number=number,
                    content=content,
                    rank_score=float(score),
                    lexical_score=lexical,
                    dense_distance=dense,
                )
                for iri, law_id, title, number, content, score, lexical, dense in cur.fetchall()
            ]
