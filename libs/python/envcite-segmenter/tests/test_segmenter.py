"""The segmenter's contract, which both the importer and the query path depend on.

The property that matters is not segmentation quality — that is measured by
`scripts/measure-segmentation.py` against real statutes. What is tested here is
that the same lexicon yields the same `tokenizer_id` and the same tokens, because
the index side and the query side agreeing is what makes the lexical arm work at
all. A drift between them does not raise; it returns a shorter result list.
"""

from __future__ import annotations

from envcite_segmenter import ALGORITHM, Segmenter, lexicon_digest, tokenizer_id


def test_statutory_terms_survive_whole() -> None:
    """The whole point of the lexicon."""
    plain = Segmenter()
    with_terms = Segmenter(["事業廢棄物"])

    assert "事業廢棄物" not in plain.segment("處理事業廢棄物者")
    assert "事業廢棄物" in with_terms.segment("處理事業廢棄物者")


def test_the_rest_is_cut_into_overlapping_bigrams() -> None:
    """Without a dictionary, the text between lexicon terms is cut into adjacent
    two-character pieces; a lone character stands on its own."""
    seg = Segmenter(["事業廢棄物"])

    assert seg.segment("處理事業廢棄物者") == ["處理", "事業廢棄物", "者"]
    assert Segmenter().segment("排放標準") == ["排放", "放標", "標準"]
    assert Segmenter().segment("水") == ["水"]


def test_the_longest_term_wins_from_the_left() -> None:
    seg = Segmenter(["廢棄物", "一般廢棄物"])

    assert seg.segment("一般廢棄物") == ["一般廢棄物"]
    assert seg.segment("廢棄物") == ["廢棄物"]


def test_terms_with_fullwidth_parentheses_survive_whole() -> None:
    """jieba could not produce a token across 「（」; a lexicon match can."""
    seg = Segmenter(["廢（污）水處理設施"])

    assert seg.segment("設置廢（污）水處理設施") == ["設置", "廢（污）水處理設施"]


def test_ascii_runs_are_lowercased_words_and_punctuation_is_dropped() -> None:
    seg = Segmenter()

    assert seg.segment("PM2.5 與 NOx、SO2") == ["pm2", "5", "與", "nox", "so2"]


def test_the_golden_output_pins_the_algorithm() -> None:
    """Changing how text is cut must bump `ALGORITHM`, or indexed tsvectors and new
    queries silently disagree. This fails when the output changes without it."""
    seg = Segmenter(["空氣污染物"])
    text = "公私場所排放空氣污染物，應符合排放標準。"

    assert seg.segment(text) == [
        "公私", "私場", "場所", "所排", "排放", "空氣污染物",
        "應符", "符合", "合排", "排放", "放標", "標準",
    ]  # fmt: skip
    assert ALGORITHM == "lexbigram-1"


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
    assert value.startswith(f"{ALGORITHM}+")
    assert "+dict-" in value


def test_two_segmenters_with_the_same_lexicon_agree() -> None:
    """Index side and query side are different processes."""
    a = Segmenter(["空氣污染物", "排放標準"])
    b = Segmenter(["排放標準", "空氣污染物"])

    assert a.tokenizer_id == b.tokenizer_id
    text = "公私場所排放空氣污染物不得超過排放標準"
    assert a.segment(text) == b.segment(text)


def test_a_segmenter_is_not_affected_by_another() -> None:
    """A segmenter holds its own lexicon, so a second component with other terms
    cannot change this one's output."""
    plain = Segmenter()
    before = plain.segment("處理事業廢棄物者")

    Segmenter(["事業廢棄物"])

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
