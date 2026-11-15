"""法規語料查詢的值型別：自然鍵、各查詢的結果列，與檢索命中。"""

from dataclasses import dataclass

#: 查詢與推論讀的語言版本（ISO 639-3）。
LANGUAGE = "zho"


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
    """某日有效的法規版本：自然鍵、IRI、標題、語言與制定日。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    language: str
    #: 制定日（ISO 日期）；語料沒有制定事件則為空字串。
    date_document: str


@dataclass(frozen=True, slots=True)
class ArticleDetailRecord:
    """依條文版本標準 IRI（索引）查得的單一條文全文與中繼資料。"""

    iri: str
    jurisdiction: str
    id_scheme: str
    local_id: str
    law_title: str
    number: str
    content: str
    language: str
    date_document: str
    norm_kind: str
    document_type: str = ""
    repealed_on: str | None = None


@dataclass(frozen=True, slots=True)
class LawSummaryRecord:
    """語料庫中某日有效的一部法規摘要，含位階與已解析之授權母法自然鍵。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    norm_kind: str
    date_document: str
    parent_jurisdiction: str | None = None
    parent_id_scheme: str | None = None
    parent_local_id: str | None = None
    document_type: str = ""
    child_count: int = 0
    parent_count: int = 0
    guidance_count: int = 0


@dataclass(frozen=True, slots=True)
class LawGraphNodeRecord:
    """授權知識圖譜中的一個法規節點（含凱爾森位階層級與統計摘要）。"""

    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    norm_kind: str
    document_type: str
    date_document: str
    hierarchy_tier: int
    total_children: int
    truncated_children: int
    article_count: int
    invalidation_count: int
    guidance_count: int


@dataclass(frozen=True, slots=True)
class LawGraphEdgeRecord:
    """授權知識圖譜中的一條有向授權邊（法律→命令，或命令→再授權子規則）。"""

    source_jurisdiction: str
    source_id_scheme: str
    source_local_id: str
    target_jurisdiction: str
    target_id_scheme: str
    target_local_id: str
    kind: str
    anchor_articles: tuple[str, ...]
    all_rooted: bool


@dataclass(frozen=True, slots=True)
class ArticleViolationLinkRecord:
    """法規內部罰則與義務／授權條文之連動關係。"""

    penalty_article: str
    penalty_path: str
    kind: str
    target_article: str
    target_path: str


@dataclass(frozen=True, slots=True)
class GuidanceLinkRecord:
    """解釋法規條文且於基準日仍有效之行政函釋摘要（行政程序法第 159 條）。"""

    article_number: str
    path: str
    document_no: str
    issuer: str
    issued_on: str
    subject: str
    source_url: str


@dataclass(frozen=True, slots=True)
class ChildGroupCountRecord:
    """直接下位授權法規依《中央法規標準法》定名種類統計之數量。"""

    document_type: str
    count: int


@dataclass(frozen=True, slots=True)
class LawGraphRecord:
    """`GetLawGraph` 查詢結果（焦點子圖或核心母法總覽）。"""

    focus_jurisdiction: str | None
    focus_id_scheme: str | None
    focus_local_id: str | None
    nodes: tuple[LawGraphNodeRecord, ...]
    edges: tuple[LawGraphEdgeRecord, ...]
    violation_links: tuple[ArticleViolationLinkRecord, ...]
    guidance_links: tuple[GuidanceLinkRecord, ...]
    child_groups: tuple[ChildGroupCountRecord, ...]


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    """某日有效的一條：版本 IRI、條號與排序鍵。"""

    iri: str
    number: str
    sort_key: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureHeadingRecord:
    """某版本的一筆結構章節（編、章、節、款、目）。"""

    kind: str
    heading_no: str
    title: str
    sort_key: tuple[int, ...]
    parent_index: int


@dataclass(frozen=True, slots=True)
class InvalidationRecord:
    """某日因違憲失效的一個條文單元，連同使它失效的裁判。"""

    ruling_no: str
    #: `corpus.ruling_kind` 的值：interpretation 或 judgment
    ruling_kind: str
    decided_on: str
    source_url: str
    excerpt: str
    path: str
    invalid_from: str
    #: 裁判只宣告條文的一部分；失效仍記在整個條文單元上。
    partial: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一筆檢索命中的條文及其分數。"""

    iri: str
    jurisdiction: str
    id_scheme: str
    local_id: str
    law_title: str
    number: str
    content: str
    lexical_score: float
    #: 語意臂的餘弦相似度；語意臂沒有命中這一條、或沒有啟用語意臂時為 None。
    semantic_similarity: float | None = None


def is_valid_law_id(value: str) -> bool:
    """法規識別碼是否可查：非空且不超過 64 字元。"""
    return bool(value and len(value) <= 64)
