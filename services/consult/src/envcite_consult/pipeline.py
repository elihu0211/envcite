"""接地管線：各階段、其順序，以及由誰決定什麼。

本服務不執行語言模型。完整的問答有五個階段，本服務擁有前三個；後兩個在呼叫端，
由呼叫端自己的模型執行：

    retrieve      由本體決定存在什麼
    ground        由程式碼指派封閉的引用集合
    assess        由規則決定這個問題到底能不能回答
    ─────────── 以上在本服務；以下在呼叫端 ───────────
    generate      由呼叫端的模型撰寫文字，只能以代碼引用
    verify        由呼叫端刪除模型自行捏造的任何代碼

模型因此仍只決定一件事——如何以交給它的證據措辭成答案。可引用什麼、能不能答，
都在它執行前就已決定，並隨證據一起交給呼叫端。

拒答是一種結果，不是錯誤：`assess` 可以終止管線，呼叫端應將其呈現出來，而非重試。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from envcite_consult.ports import Article, OntologyPort

#: 低於此值的檢索資料，視為與問題無關。
#: 這是受模型制約的值，不是領域常數：它只在產生它的評分方式下才可比較，評分方式
#: 一旦改變就必須重新推導。
RELEVANCE_FLOOR = 0.30

#: 語意臂的餘弦相似度下界（由 golden-eval / calibration 推得，超過此值視為相關）。
SEMANTIC_RELEVANCE_FLOOR = 0.65

#: 至少有一個來源達到此強度，即表示答案建立在直接的依據上。
HIGH_CONFIDENCE_FLOOR = 0.50

#: 語意臂達到此強度表示高度語意對齊。
HIGH_CONFIDENCE_SEMANTIC_FLOOR = 0.75

#: 封閉引用集合設有上限，讓證據區塊維持在模型能可靠關注的範圍內。超出的來源
#: 直接捨棄，而非加以摘要。
MAX_CITATIONS = 8


class Confidence(Enum):
    LOW = "low"
    HIGH = "high"


class RefusalReason(Enum):
    NO_EVIDENCE = "no_evidence"
    OUT_OF_SCOPE = "out_of_scope"
    MISSING_INPUT = "missing_input"


LAW = "law"

# 本體代號 → 引用代號的字母前綴。一個本體一個字母，新增本體時在此登記，
# 跨本體的代號才不會相撞。
CODE_PREFIX = {LAW: "L"}


@dataclass(frozen=True, slots=True)
class Citation:
    code: str
    iri: str
    label: str
    text: str
    ontology: str = LAW


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: RefusalReason
    detail: str


@dataclass(frozen=True, slots=True)
class Evidence:
    """呼叫端的模型可引用的封閉集合，以及其中最強依據的信心度。"""

    citations: list[Citation]
    confidence: Confidence


def is_relevant(article: Article) -> bool:
    """判定單一條文是否通過相關性下界（字面臂或語意臂任一臂達標）。"""
    if (
        article.semantic_similarity is not None
        and article.semantic_similarity >= SEMANTIC_RELEVANCE_FLOOR
    ):
        return True
    return article.score >= RELEVANCE_FLOOR


def is_high_confidence(article: Article) -> bool:
    """判定單一條文是否達到高信心強度。"""
    if (
        article.semantic_similarity is not None
        and article.semantic_similarity >= HIGH_CONFIDENCE_SEMANTIC_FLOOR
    ):
        return True
    return article.score >= HIGH_CONFIDENCE_FLOOR


def ground(articles: list[Article]) -> list[Citation]:
    """指派封閉的引用集合：依檢索順序編為 L1、L2、…。

    代碼由程式碼指派，絕不由模型指派，而這個對應關係正是引用得以查核的原因：
    `L3` 要嘛在這份清單裡，要嘛不在。
    """
    kept = [a for a in articles if is_relevant(a)][:MAX_CITATIONS]
    return [
        Citation(code=f"{CODE_PREFIX[LAW]}{i}", iri=a.iri, label=a.label, text=a.text, ontology=LAW)
        for i, a in enumerate(kept, start=1)
    ]


def assess(grounding: list[Citation], articles: list[Article]) -> Refusal | None:
    """依檢索結果決定是否作答。回傳拒答結果，或回傳 None 表示繼續。"""
    if not articles:
        return Refusal(RefusalReason.NO_EVIDENCE, "檢索無任何結果")
    if not grounding:
        # 有檢索到東西，但沒有任何一筆超過下界。這與什麼都沒檢索到是不同的失敗，
        # 呼叫端應能加以區分：前者表示語料缺漏，後者表示問題談的是別的事。
        return Refusal(RefusalReason.OUT_OF_SCOPE, "檢索結果皆未達相關性下界")
    return None


def confidence_of(grounding: list[Citation], articles: list[Article]) -> Confidence:
    by_iri = {a.iri: a for a in articles}
    has_high = any(is_high_confidence(a) for c in grounding if (a := by_iri.get(c.iri)) is not None)
    return Confidence.HIGH if has_high else Confidence.LOW


def run(
    question: str, *, as_of: str, ontology: OntologyPort, timeout: float | None = None
) -> Evidence | Refusal:
    """為一個問題備妥證據，或拒答。只檢索一次：信心度與 grounding 出自同一次結果。

    `timeout` 是呼叫端剩下的秒數，往下游傳，讓下游不超過上游的期限。
    """
    # 空白問句在檢索之前就拒答：本體對空查詢回 INVALID_ARGUMENT，而這是使用者少給了
    # 輸入，不是呼叫端的錯誤。
    if not question.strip():
        return Refusal(RefusalReason.MISSING_INPUT, "問句為空")

    articles = ontology.search(question, as_of=as_of, timeout=timeout)
    grounding = ground(articles)

    refusal = assess(grounding, articles)
    if refusal is not None:
        return refusal
    return Evidence(citations=grounding, confidence=confidence_of(grounding, articles))
