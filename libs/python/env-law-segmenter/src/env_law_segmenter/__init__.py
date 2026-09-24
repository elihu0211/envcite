"""Chinese segmentation with a lexicon derived from the corpus.

The index side and the query side must use the same segmenter. A mismatch does
not raise: the query's lexemes differ from the indexed ones, fewer rows match,
and the result list is shorter rather than wrong — the harder failure to see.
This package exists so that both sides cannot reach different implementations.

Which segmenter, and why, is measured rather than assumed: see README.md and
`scripts/measure-segmentation.py`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from importlib.metadata import version

import jieba

__all__ = ["Segmenter", "lexicon_digest", "tokenizer_id"]


def lexicon_digest(terms: Iterable[str]) -> str:
    """A stable short digest of the lexicon's content.

    Sorted and de-duplicated first, so the digest depends on which terms are in
    the lexicon and not on the order they arrived in — otherwise re-running the
    importer would appear to change the tokenizer.
    """
    joined = "\n".join(sorted({t for t in terms if t}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def tokenizer_id(terms: Iterable[str]) -> str:
    """The value that goes into `subdivision.tokenizer`.

    Carries both halves of what determines a segmentation: the implementation
    and the lexicon. A change in either makes previously indexed tsvectors
    incomparable with new queries, which is what the column exists to detect.
    """
    return f"jieba-{version('jieba')}+dict-{lexicon_digest(terms)}"


class Segmenter:
    """Segments text the same way every time, given the same lexicon.

    Holds its own `jieba.Tokenizer` rather than using the module-level default:
    the default is process-global mutable state, and a second component adding a
    word to it would silently change this one's output.
    """

    def __init__(self, terms: Sequence[str] = ()) -> None:
        self._terms = tuple(sorted({t for t in terms if t}))
        self._tokenizer = jieba.Tokenizer()
        self._tokenizer.initialize()
        for term in self._terms:
            # A frequency high enough that the term is never split in favour of
            # its parts. The statute defined it as one thing; the segmenter has
            # no basis to disagree.
            self._tokenizer.add_word(term, freq=100_000)

        # Known and measured limitation: jieba's `re_han` excludes full-width
        # brackets, so it cannot emit a token spanning one, whatever the
        # dictionary says. 「廢（污）水處理設施」 and 「自然保護（育）區」 are
        # statutory terms written exactly that way, and they are the entire
        # residual error — 11 occurrences out of 1036, the 1.1% that the 98.9%
        # leaves. Registering a bracket-free variant does nothing, because the
        # text being segmented has the brackets; an earlier version of this file
        # did that and looked like it was handling the case.
        #
        # Fixing it means overriding jieba's module-level `re_han_default`, which
        # is process-global state this class deliberately avoids. Left alone
        # until the 1.1% is worth that trade.

    @property
    def tokenizer_id(self) -> str:
        return tokenizer_id(self._terms)

    @property
    def terms(self) -> tuple[str, ...]:
        return self._terms

    def tokenize(self, text: str) -> list[tuple[str, int, int]]:
        """Words with their character offsets: (word, start, end).

        Offsets come from jieba rather than being reconstructed by summing token
        lengths. Reconstruction breaks the moment any token is filtered out —
        which `segment()` does to whitespace — and the breakage is silent: every
        position after the first dropped token is wrong, so a checker comparing
        positions reports correct segmentations as failures. That happened here,
        and it moved a headline number by thirty points.
        """
        return list(self._tokenizer.tokenize(text))

    def segment(self, text: str) -> list[str]:
        """Words, with whitespace dropped.

        Whitespace is dropped rather than kept as tokens because the output is
        joined by spaces downstream; keeping it would produce empty lexemes. Use
        `tokenize()` when positions matter.
        """
        return [w for w in self._tokenizer.cut(text) if w.strip()]

    def to_tsvector_input(self, text: str) -> str:
        """Space-separated words, ready for `to_tsvector('simple', …)`.

        The database never segments — it receives words. This is the only place
        that decides what a word is.
        """
        return " ".join(self.segment(text))
