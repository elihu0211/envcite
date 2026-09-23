"""The consultation pipeline: the stages, their order, and who decides what.

The shape is the point. Read top to bottom and it should be obvious that the
language model decides exactly one thing — how to phrase an answer out of
evidence it was handed — and that every other decision is made before it runs or
checked after it returns:

    retrieve      ontology decides what exists
    ground        code assigns the closed citation set
    assess        rules decide whether the question is answerable at all
    generate      the model writes prose, citing only by code
    verify        code strips any code the model invented

A refusal is an outcome, not an error: `assess` can end the pipeline, and callers
render that rather than retrying it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from env_law.consult.ports import Article, LlmPort, OntologyPort

#: Below this, retrieved material is treated as not bearing on the question.
#: Model-conditioned, not a domain constant: it is comparable only under the
#: scoring that produced it, and must be re-derived whenever that changes.
RELEVANCE_FLOOR = 0.30

#: At least one source this strong means the answer rests on something direct.
HIGH_CONFIDENCE_FLOOR = 0.50

#: The closed citation set is capped so the evidence block stays within what a
#: model reliably attends to. Sources beyond this are dropped, not summarised.
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
    """What a run produced. Exactly one of `refusal` / `chunks` is meaningful."""

    grounding: list[Citation] = field(default_factory=list)
    refusal: Refusal | None = None
    chunks: Iterator[str] | None = None


def ground(articles: list[Article]) -> list[Citation]:
    """Assigns the closed citation set: L1, L2, … in retrieval order.

    Codes are assigned by code, never by the model, and the mapping is what makes
    a citation checkable: `L3` either is in this list or it is not.
    """
    kept = [a for a in articles if a.score >= RELEVANCE_FLOOR][:MAX_CITATIONS]
    return [
        Citation(code=f"L{i}", iri=a.iri, label=a.label, text=a.text)
        for i, a in enumerate(kept, start=1)
    ]


def assess(question: str, grounding: list[Citation], articles: list[Article]) -> Refusal | None:
    """Decides whether to answer at all. Returns the refusal, or None to proceed."""
    if not question.strip():
        return Refusal(RefusalReason.MISSING_INPUT, "問句為空")
    if not articles:
        return Refusal(RefusalReason.NO_EVIDENCE, "檢索無任何結果")
    if not grounding:
        # Something was retrieved but nothing cleared the floor. That is a
        # different failure from retrieving nothing, and the caller should be able
        # to tell them apart: one means the corpus lacks it, the other means the
        # question is about something else.
        return Refusal(RefusalReason.OUT_OF_SCOPE, "檢索結果皆未達相關性下界")
    return None


def confidence_of(grounding: list[Citation], articles: list[Article]) -> Confidence:
    by_iri = {a.iri: a.score for a in articles}
    top = max((by_iri.get(c.iri, 0.0) for c in grounding), default=0.0)
    return Confidence.HIGH if top >= HIGH_CONFIDENCE_FLOOR else Confidence.LOW


def render_evidence(grounding: list[Citation]) -> str:
    """The evidence block handed to the model. Codes are the only handle it gets."""
    return "\n\n".join(f"[{c.code}] {c.label}\n{c.text}" for c in grounding)


def cited_codes(text: str, grounding: list[Citation]) -> list[str]:
    """Codes actually present in the finished answer, in the grounding's order."""
    return [c.code for c in grounding if f"[{c.code}]" in text]


def run(question: str, *, as_of: str, ontology: OntologyPort, llm: LlmPort) -> Outcome:
    """Runs one consultation. Streaming begins only after grounding is fixed."""
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
