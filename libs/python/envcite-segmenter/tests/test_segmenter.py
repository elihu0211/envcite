"""The segmenter's contract, which both the importer and the query path depend on.

The property that matters is not segmentation quality — that is measured by
`scripts/measure-segmentation.py` against real statutes. What is tested here is
that the same lexicon yields the same `tokenizer_id` and the same tokens, because
the index side and the query side agreeing is what makes the lexical arm work at
all. A drift between them does not raise; it returns a shorter result list.
"""

from __future__ import annotations

from envcite_segmenter import Segmenter, lexicon_digest, tokenizer_id


def test_statutory_terms_survive_whole() -> None:
    """The whole point of the lexicon."""
    plain = Segmenter()
    with_terms = Segmenter(["事業廢棄物"])

    assert "事業廢棄物" not in plain.segment("處理事業廢棄物者")
    assert "事業廢棄物" in with_terms.segment("處理事業廢棄物者")


def test_the_digest_ignores_order_and_duplicates() -> None:
    """Re-running the importer must not appear to change the tokenizer."""
    assert lexicon_digest(["b", "a"]) == lexicon_digest(["a", "b", "a"])


def test_the_digest_changes_with_content() -> None:
    """A lexicon change makes previously indexed tsvectors incomparable, and the
    id is what detects that."""
    assert lexicon_digest(["a"]) != lexicon_digest(["a", "b"])


def test_empty_terms_are_ignored() -> None:
    assert lexicon_digest(["a", ""]) == lexicon_digest(["a"])


def test_the_id_names_both_halves() -> None:
    """Implementation and lexicon both determine a segmentation."""
    value = tokenizer_id(["事業廢棄物"])
    assert value.startswith("jieba-")
    assert "+dict-" in value


def test_two_segmenters_with_the_same_lexicon_agree() -> None:
    """Index side and query side are different processes."""
    a = Segmenter(["空氣污染物", "排放標準"])
    b = Segmenter(["排放標準", "空氣污染物"])

    assert a.tokenizer_id == b.tokenizer_id
    text = "公私場所排放空氣污染物不得超過排放標準"
    assert a.segment(text) == b.segment(text)


def test_a_segmenter_is_not_affected_by_another() -> None:
    """jieba's module-level tokenizer is global mutable state; this class holds
    its own, so a second component adding a word cannot change this one's output."""
    plain = Segmenter()
    before = plain.segment("處理事業廢棄物者")

    Segmenter(["事業廢棄物"])  # would mutate a shared dictionary

    assert plain.segment("處理事業廢棄物者") == before


def test_offsets_come_from_the_tokenizer() -> None:
    """Reconstructing offsets by summing lengths breaks wherever a token is
    filtered out, and breaks silently — every later position is wrong."""
    seg = Segmenter(["事業廢棄物"])
    text = "處理 事業廢棄物 者"
    tokens = seg.tokenize(text)

    for word, start, end in tokens:
        assert text[start:end] == word

    assert any(word == "事業廢棄物" for word, _s, _e in tokens)


def test_tsvector_input_drops_whitespace() -> None:
    """Empty lexemes would otherwise reach to_tsvector."""
    seg = Segmenter()
    out = seg.to_tsvector_input("公私 場所\n排放")
    assert "  " not in out
    assert out.split(" ") == [w for w in out.split(" ") if w]
