"""資料層的介面（Protocol）：消費端只依賴它用到的那一塊，實作與連線由組合根接上。

每個 Protocol 對應一個儲存庫（`laws`、`graph`、`retrieval`）或連線來源（`connections`）；
測試與其他呼叫端可以換成自己的實作，不必碰資料庫。
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

import psycopg

from envcite_ontology.law_names import LawNames
from envcite_ontology.store.records import (
    ArticleDetailRecord,
    ArticleRecord,
    InvalidationRecord,
    LawGraphRecord,
    LawKey,
    LawRecord,
    LawSummaryRecord,
    SearchHit,
    StructureHeadingRecord,
)

#: 唯讀連線的列保持未定型（`read_connection` 的說明）。
type ReadConnection = psycopg.Connection[tuple[Any, ...]]


class ReadConnections(Protocol):
    """唯讀連線的來源：期限同時是等連線的上限與 `statement_timeout`。"""

    def read_connection(
        self, *, timeout: float | None = None
    ) -> AbstractContextManager[ReadConnection]:
        """唯讀連線，在一個交易內（結束時一律回滾）。"""
        ...


class ConnectionSource(ReadConnections, Protocol):
    """唯讀連線之外，還能給啟動時讀一次的查詢（詞典、法規名稱、列舉）一條普通連線。"""

    def connection(self) -> AbstractContextManager[ReadConnection]:
        """池裡的一條連線，沒有交易外的限制。"""
        ...


class LawReader(Protocol):
    """法規與條文的唯讀查詢。"""

    def get_law(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> LawRecord | None:
        """`as_of`（ISO 日期，空為今日）有效版本的法規；法規不存在或該日無版本時為 None。"""
        ...

    def get_article_by_iri(
        self, iri: str, *, timeout: float | None = None
    ) -> ArticleDetailRecord | None:
        """依條文版本標準 IRI 查詢單一條文全文與中繼資料；格式不符或不存在時回 None。"""
        ...

    def list_laws(
        self,
        *,
        as_of: str = "",
        jurisdiction: str | None = None,
        category_prefix: str | None = None,
        timeout: float | None = None,
    ) -> list[LawSummaryRecord]:
        """列出 `as_of` 當日有效且有全文的法規摘要與授權統計。"""
        ...

    def list_articles(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[ArticleRecord] | None:
        """`as_of` 當日有效的各條；法規或版本不存在為 None，有版本而無有效條為 []，兩者語意不同。"""
        ...

    def list_headings(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> list[StructureHeadingRecord] | None:
        """`as_of` 當日有效版本的結構章節；法規或版本不存在為 None。"""
        ...

    def list_invalidations(
        self,
        key: LawKey,
        *,
        as_of: str = "",
        article_number: str = "",
        timeout: float | None = None,
    ) -> list[InvalidationRecord] | None:
        """`as_of` 當日因違憲而失效的條文單元；法規或版本不存在為 None，沒有失效為 []。"""
        ...

    def law_exists(self, key: LawKey, *, timeout: float | None = None) -> bool:
        """法規在語料裡有沒有，不論哪一天。區分「查無此法」與「該日無版本」。"""
        ...

    def resource_id(
        self, key: LawKey, *, as_of: str = "", timeout: float | None = None
    ) -> int | None:
        """`as_of` 有效版本的法規之內部編號；法規不存在或該日無版本時為 None。"""
        ...

    @property
    def jurisdictions(self) -> frozenset[str]:
        """綱要接受的管轄（`corpus.jurisdiction`）；封閉集合，啟動後第一次用到時讀一次。"""
        ...

    @property
    def id_schemes(self) -> frozenset[str]:
        """綱要接受的識別碼方案（`corpus.id_scheme`）；讀取時機同 `jurisdictions`。"""
        ...


class LawGraphReader(Protocol):
    """多層授權知識圖譜的唯讀查詢。"""

    def get_law_graph(
        self,
        key: LawKey | None,
        *,
        as_of: str = "",
        category_prefix: str | None = None,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
        child_limit: int = 20,
        child_document_type: str = "",
        timeout: float | None = None,
    ) -> LawGraphRecord | None:
        """取得多層授權知識圖譜（焦點子圖或總覽）；指定法規無版本時為 None。"""
        ...


class Retrieval(Protocol):
    """混合檢索：字面臂與語意臂、函釋入口，與斷詞用的詞典與法規名稱。"""

    def lexicon(self) -> list[str]:
        """segmenter 的詞典：語料所有版本的法規標題（不重複）。"""
        ...

    def law_names(self) -> LawNames:
        """法規標題（含各版本的舊名稱）對法規代碼的對照，供問句點名法規用。"""
        ...

    def guidance_hits(
        self,
        phrases: Sequence[str],
        *,
        as_of: str = "",
        limit: int = 10,
        jurisdiction: str | None = None,
        max_df: float = 0.0,
        timeout: float | None = None,
    ) -> list[tuple[SearchHit, int]]:
        """有效函釋（主旨或內文含每個詞組）所解釋的條文，連同函釋件數；多的在前。"""
        ...

    def searched_terms(
        self, terms: str, *, tokenizer: str, max_df: float = 0.0, timeout: float | None = None
    ) -> list[str]:
        """字面臂實際查的詞：`search` 以同樣的 `max_df` 略過高頻詞之後留下的詞。"""
        ...

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
        """`as_of`（空為今日）有效的條，字面臂比對已分詞的 `terms`，語意臂比對 `embedding`。"""
        ...
