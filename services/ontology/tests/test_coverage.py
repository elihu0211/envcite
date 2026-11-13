"""term_coverage：條文含有多少比例的查詢詞。"""

import pytest

from envcite_ontology.coverage import term_coverage
from envcite_segmenter import Segmenter

SEGMENTER = Segmenter(())


def test_an_article_holding_every_term_covers_the_query() -> None:
    assert term_coverage(["露天", "天燃", "燃燒"], "禁止露天燃燒廢棄物", SEGMENTER) == 1.0


def test_a_fragment_covers_only_its_share() -> None:
    assert term_coverage(
        ["露天", "天燃", "燃燒"], "露天甲板應設置欄杆", SEGMENTER
    ) == pytest.approx(1 / 3)


def test_terms_are_counted_once() -> None:
    assert term_coverage(["露天", "露天", "星球"], "露天甲板", SEGMENTER) == 0.5


def test_no_query_terms_cover_nothing() -> None:
    assert term_coverage([], "露天甲板", SEGMENTER) == 0.0
