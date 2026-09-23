"""Ports the pipeline depends on.

Both are Protocols, not clients. The pipeline's shape — which stage may decide
what — is the thing this service exists to fix in place, and that shape must be
testable before either a language model endpoint or a running ontology service
exists. Implementations live in `adapters.py`; the fakes in the tests are the
other implementation, and they are not lesser.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Article:
    """One retrieved subdivision. `score` is comparable only within one retrieval."""

    iri: str
    label: str
    text: str
    score: float


class OntologyPort(Protocol):
    """Read access to the law. The only source of legal fact in this service."""

    def search(self, query: str, *, as_of: str = "", limit: int = 10) -> list[Article]: ...


class LlmPort(Protocol):
    """Generation, constrained to a closed set of citation codes.

    `allowed_codes` is not advice. The generated text is checked against it
    downstream and anything outside is stripped, so a model that ignores the
    constraint degrades the answer rather than corrupting it.
    """

    def generate(
        self, *, question: str, evidence: str, allowed_codes: list[str]
    ) -> Iterator[str]: ...
