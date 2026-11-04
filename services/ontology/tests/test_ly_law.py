from datetime import date

import pytest

from envcite_ontology.acquire import ly_law
from envcite_ontology.ingest.backfill import UnreadablePage
from tests import ly_site

V1, V2 = date(2005, 2, 5), date(2011, 11, 23)


def _page() -> str:
    return ly_site.law_page(
        "行政罰法", V2, [(date(2005, 1, 14), V1), (date(2011, 11, 8), V2)], [(1, "甲。\n一、乙。")]
    )


def test_entries_carry_the_promulgation_date_and_a_session_free_key() -> None:
    entries = ly_law.entries(_page())
    assert [(e.passed, e.promulgated, e.key) for e in entries] == [
        (date(2005, 1, 14), V1, f"{ly_site.LAW_NO}0940114"),
        (date(2011, 11, 8), V2, f"{ly_site.LAW_NO}1001108"),
    ]


def test_a_version_is_dated_by_promulgation_with_one_line_per_paragraph_and_item() -> None:
    v = ly_law.parse_version(ly_law.fragment(_page()), V2)
    assert (v.title, v.version_date) == ("行政罰法", V2)
    assert [(a.article_no, a.content) for a in v.articles] == [("第 1 條", "甲。\n一、乙。")]


def test_article_numbers_with_suffixes() -> None:
    page = _page().replace("第一條", "第一百零二條之一")
    (a,) = ly_law.parse_version(ly_law.fragment(page), V2).articles
    assert a.article_no == "第 102-1 條"


def test_the_fragment_leaves_out_session_codes() -> None:
    assert "lawsingle" not in ly_law.fragment(_page())
    attached = _page().replace(
        "甲。", "甲。<br>\n<a href=/lglawc/lawsingle?{}^09999094011400^000200>附表</a>"
    )
    one, two = (ly_law.fragment(attached.format(code)) for code in ("01132A8AAAF8", "00844418D39C"))
    assert one == two


def test_hits_strip_the_highlight() -> None:
    (hit,) = ly_law.hits(ly_site.results([("行政罰法", date(2022, 5, 30), date(2022, 6, 15))]))
    assert (hit.title, hit.promulgated) == ("行政罰法", date(2022, 6, 15))


def test_no_result_is_an_empty_list() -> None:
    assert ly_law.hits("<td>※ 無檢索結果 ※[(海關進口稅則)@LN,LW]</td>" + ly_site.home()) == []


def test_unexpected_pages_are_unreadable() -> None:
    for parse in (ly_law.hits, ly_law.entries, ly_law.fragment):
        with pytest.raises(UnreadablePage):
            parse("<html>系統維護中</html>")
    with pytest.raises(UnreadablePage):
        ly_law.search_form("<html></html>", "行政罰法")


def test_the_search_name_drops_the_disambiguating_note() -> None:
    assert ly_law.name("最低工資法（112.12.27制定）") == "最低工資法"
    assert ly_law.name("總統副總統選舉罷免法（新 84.08.09 制定）") == "總統副總統選舉罷免法"
    assert ly_law.name("行政罰法") == "行政罰法"
