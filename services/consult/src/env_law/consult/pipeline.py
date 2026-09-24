"""諮詢管線：各階段、其順序，以及由誰決定什麼。

重點在於這個形狀。由上往下讀，應當一目了然：語言模型只決定一件事——如何以交給它
的證據措辭成答案——其餘每一個決定，不是在它執行前就已做出，就是在它回傳後加以檢查：

    retrieve      由本體決定存在什麼
    ground        由程式碼指派封閉的引用集合
    assess        由規則決定這個問題到底能不能回答
    generate      由模型撰寫文字，只能以代碼引用
    verify        由程式碼刪除模型自行捏造的任何代碼

拒答是一種結果，不是錯誤：`assess` 可以終止管線，呼叫端應將其呈現出來，而非重試。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from env_law.consult.ports import Article, LlmPort, OntologyPort

#: 低於此值的檢索資料，視為與問題無關。
#: 這是受模型制約的值，不是領域常數：它只在產生它的評分方式下才可比較，評分方式
#: 一旦改變就必須重新推導。
RELEVANCE_FLOOR = 0.30

#: 至少有一個來源達到此強度，即表示答案建立在直接的依據上。
HIGH_CONFIDENCE_FLOOR = 0.50

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


@dataclass(frozen=True, slots=True)
class Citation:
    code: str
    iri: str
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: RefusalReason
    detail: str


@dataclass
class Outcome:
    """一次執行的產出。`refusal` 與 `chunks` 恰有一個有意義。"""

    grounding: list[Citation] = field(default_factory=list)
    refusal: Refusal | None = None
    chunks: Iterator[str] | None = None


def ground(articles: list[Article]) -> list[Citation]:
    """指派封閉的引用集合：依檢索順序編為 L1、L2、…。

    代碼由程式碼指派，絕不由模型指派，而這個對應關係正是引用得以查核的原因：
    `L3` 要嘛在這份清單裡，要嘛不在。
    """
    kept = [a for a in articles if a.score >= RELEVANCE_FLOOR][:MAX_CITATIONS]
    return [
        Citation(code=f"L{i}", iri=a.iri, label=a.label, text=a.text)
        for i, a in enumerate(kept, start=1)
    ]


def assess(question: str, grounding: list[Citation], articles: list[Article]) -> Refusal | None:
    """決定是否作答。回傳拒答結果，或回傳 None 表示繼續。"""
    if not question.strip():
        return Refusal(RefusalReason.MISSING_INPUT, "問句為空")
    if not articles:
        return Refusal(RefusalReason.NO_EVIDENCE, "檢索無任何結果")
    if not grounding:
        # 有檢索到東西，但沒有任何一筆超過下界。這與什麼都沒檢索到是不同的失敗，
        # 呼叫端應能加以區分：前者表示語料缺漏，後者表示問題談的是別的事。
        return Refusal(RefusalReason.OUT_OF_SCOPE, "檢索結果皆未達相關性下界")
    return None


def confidence_of(grounding: list[Citation], articles: list[Article]) -> Confidence:
    by_iri = {a.iri: a.score for a in articles}
    top = max((by_iri.get(c.iri, 0.0) for c in grounding), default=0.0)
    return Confidence.HIGH if top >= HIGH_CONFIDENCE_FLOOR else Confidence.LOW


def render_evidence(grounding: list[Citation]) -> str:
    """交給模型的證據區塊。代碼是它唯一能用的把手。"""
    return "\n\n".join(f"[{c.code}] {c.label}\n{c.text}" for c in grounding)


def cited_codes(text: str, grounding: list[Citation]) -> list[str]:
    """完成的答案中實際出現的代碼，依 grounding 的順序排列。"""
    return [c.code for c in grounding if f"[{c.code}]" in text]


def run(question: str, *, as_of: str, ontology: OntologyPort, llm: LlmPort) -> Outcome:
    """執行一次諮詢。串流只在 grounding 確定之後才開始。"""
    articles = ontology.search(question, as_of=as_of)
    grounding = ground(articles)

    refusal = assess(question, grounding, articles)
    if refusal is not None:
        return Outcome(grounding=grounding, refusal=refusal)

    chunks = llm.generate(
        question=question,
        evidence=render_evidence(grounding),
        allowed_codes=[c.code for c in grounding],
    )
    return Outcome(grounding=grounding, chunks=chunks)
