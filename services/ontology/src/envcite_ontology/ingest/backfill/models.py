"""回推的資料型別：來源的版本、語料裡的錨點，與要寫入的計畫。"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from envcite_ontology.import_problems import Problem
from envcite_ontology.ingest.law import ParsedLaw, ParsedUnit


class UnreadablePage(ValueError):
    """來源的頁面抓到了，卻不是預期的形狀。"""


@dataclass(frozen=True, slots=True)
class Article:
    """來源版本裡的一條：條號、各項款目的全文，與轉錄品質標記。"""

    article_no: str  # 「第 N 條」「第 N-M 條」
    #: 項、款、目各一行
    content: str
    #: 版面排不下、全文只在附件（PDF）
    incomplete: bool
    #: 轉錄還原時判不準的分項處（來源是折行轉錄者才有）
    ambiguous: int


@dataclass(frozen=True, slots=True)
class Version:
    """來源的一個歷次版本；版本日是公布或發布日，與沿革同。"""

    title: str
    version_date: date
    articles: list[Article]


@dataclass(frozen=True)
class Anchor:
    """語料裡這部法規的現況：回推的終點。"""

    resource: int
    pcode: str
    kind: str
    repealed_on: date | None
    #: 最早的官方資料庫版本
    version_date: date
    #: 最新版本（主管機關系統的現行頁要與它同日）
    latest_date: date
    #: 錨點各單元現行列的效力起點（未知為 None）
    starts: dict[str, date | None]
    #: 錨點之後每個官方資料庫版本各單元現行列的效力起點，由舊到新。每日同步在回推之前已經入庫
    #: 的新版本：錨點的起點待確認而新版本有起點時，更舊的版本要止於它，否則區間開放而與它重疊。
    after: list[dict[str, date | None]]
    #: 錨點各條的全文（條號 `第 N 條` → 文字）
    articles: dict[str, str]
    #: 最新版本各條的全文，用來核對還原的轉錄
    latest_articles: dict[str, str]
    #: 沿革（`corpus.lifecycle_event` 的原文，依原順序）
    events: list[str]


@dataclass(frozen=True)
class Planned:
    """回推要寫入的一個歷史版本；查不到全文的版本沒有標題與條文單元。"""

    version_date: date
    #: 查不到全文的版本（`units` 為空）不知道當時的標題
    title: str | None
    #: 文字時間的上界：下一個版本的版本日
    until: date
    units: list[ParsedUnit]

    @property
    def status(self) -> str:
        """寫入 `text_status` 的值：有標題（查得到全文）為 `available`，否則 `unavailable`。"""
        return "available" if self.title is not None else "unavailable"


@dataclass
class Plan:
    """回推計畫：要寫入的各版本、沿途發現的問題，與現行頁的核對結果。"""

    versions: list[Planned] = field(default_factory=list)  # 舊到新
    problems: list[Problem] = field(default_factory=list)
    #: 每一版當作現行版解析的結果，供整批驗收
    parsed: list[ParsedLaw] = field(default_factory=list)
    #: 現行頁與全國法規資料庫現行文字的核對：
    #: exact、typography、structure、differs、incomplete、missing
    check: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        """可寫入：沒有 `error` 等級的問題。"""
        return not any(p.severity == "error" for p in self.problems)
