"""環境部主管法規查詢系統的頁面解析：折行還原、欄位與清單。"""

from datetime import date

import pytest

from envcite_ontology.acquire import moenv_law
from envcite_ontology.ingest import backfill
from tests.moenv_site import current_page, history_list, history_page, listing

#: 行寬 64（全形 32 字）的轉錄，取自空氣污染防制法民國 101 年版第 2 條與 107 年版第 8 條的寫法。
WRAPPED = [
    "本法專用名詞定義如下：",
    "一、空氣污染物：指空氣中足以直接或間接妨害國民健康或生活環境之物",
    "    質。",
    "二、生活環境：指與人之生活有密切關係之財產、動、植物及其生育環境",
    "    。",
    "中央主管機關得依地形、氣象條件，將空氣污染物互相流通之一個或多個",
    "直轄市、縣（巿）指定為總量管制區，訂定總量管制計畫，公告實施總量",
    "管制。",
]


def test_wrapped_lines_are_joined_back_into_paragraphs_and_subparagraphs() -> None:
    text, ambiguous = moenv_law.unwrap(WRAPPED, 64)

    assert text.split("\n") == [
        "本法專用名詞定義如下：",
        "一、空氣污染物：指空氣中足以直接或間接妨害國民健康或生活環境之物質。",
        "二、生活環境：指與人之生活有密切關係之財產、動、植物及其生育環境。",
        "中央主管機關得依地形、氣象條件，將空氣污染物互相流通之一個或多個直轄市、縣（巿）"
        "指定為總量管制區，訂定總量管制計畫，公告實施總量管制。",
    ]
    assert ambiguous == 0


def test_a_full_line_ending_a_sentence_starts_a_new_paragraph_and_is_counted() -> None:
    lines = [
        "符合空氣品質標準之總量管制區，新設或變更之固定污染源污染物排放量",
        "達一定規模者，須經模式模擬證明不超過該區之污染物容許增量限值。",
        "未符合空氣品質標準之總量管制區，既存之固定污染源應申請認可。",
    ]
    text, ambiguous = moenv_law.unwrap(lines, 64)

    assert text.count("\n") == 1
    assert text.endswith("\n未符合空氣品質標準之總量管制區，既存之固定污染源應申請認可。")
    assert ambiguous == 1


def test_a_proviso_after_a_full_line_continues_the_paragraph() -> None:
    lines = [
        "既存之固定污染源因採行防制措施致實際削減量較指定為多者，得保留。",
        "但無法達成指定削減目標者，應取得抵換之排放量。",
    ]
    text, _ = moenv_law.unwrap(lines, 64)

    assert "\n" not in text


def test_layout_lines_and_tables_are_kept_as_they_are() -> None:
    lines = [
        "排放標準如下：",
        "┌────────┬───────┐",
        "│    污染源      │排放標準值    │",
        "└────────┴───────┘",
        "               21-On",
        "           C ＝───‧Cs",
    ]
    text, _ = moenv_law.unwrap(lines, 64)

    assert text.split("\n") == lines


def test_a_page_without_any_wrapped_line_is_not_joined() -> None:
    text, _ = moenv_law.unwrap(["第一項。", "第二項。"], 20)

    assert text == "第一項。\n第二項。"


def test_current_and_history_pages_give_title_date_and_articles() -> None:
    current = moenv_law.parse_version(
        current_page("噪音管制法", date(2020, 3, 1), [("1", "甲。"), ("2-1", "乙。")])
    )
    past = moenv_law.parse_version(history_page("噪音管制法", date(1991, 5, 23), [("1", "甲。")]))

    assert (current.title, current.version_date) == ("噪音管制法", date(2020, 3, 1))
    assert [a.article_no for a in current.articles] == ["第 1 條", "第 2-1 條"]
    assert (past.version_date, past.articles[0].content) == (date(1991, 5, 23), "甲。")


def test_an_article_only_in_pdf_is_marked_incomplete() -> None:
    page = current_page("噪音管制法", date(2020, 3, 1), [("1", "甲。")]).replace(
        "<b>第 1 條</b>", "<b>第 1 條（因條文排版無法完整呈現內容，請詳閱完整條文檔案）</b>"
    )

    assert moenv_law.parse_version(page).articles[0].incomplete


def test_listing_history_and_fragment() -> None:
    rows = moenv_law.list_rows(
        listing([("FL000001", "噪音管制法", "法律"), ("GL1", "某要點", "行政規則")])
    )
    page = current_page("噪音管制法", date(2020, 3, 1), [("1", "甲。")])

    assert [(r.site_id, r.title, r.kind) for r in rows] == [
        ("FL000001", "噪音管制法", "法律"),
        ("GL1", "某要點", "行政規則"),
    ]
    assert moenv_law.history_ids(history_list("FL000001", [12, 11])) == [12, 11]
    assert moenv_law.history_ids(history_list("FL000001", [])) == []  # 從未修正
    with pytest.raises(backfill.UnreadablePage):
        moenv_law.history_ids("<html>維護中</html>")
    # 瀏覽人數每次不同：不在片段裡，同一份內容的雜湊才不變。
    assert "瀏覽人數" not in moenv_law.fragment(page)
    assert moenv_law.parse_version(moenv_law.fragment(page)).version_date == date(2020, 3, 1)


def test_a_page_of_another_shape_is_unreadable() -> None:
    with pytest.raises(backfill.UnreadablePage):
        moenv_law.parse_version("<html>維護中</html>")


def test_fold_ignores_width_and_spacing_differences_between_the_two_systems() -> None:
    assert backfill.fold("縣 (市) 低於 10-10  公分") == backfill.fold("縣（市）低於10-10公分")


def test_pages_edited_with_the_rich_text_editor_use_strong_and_spans() -> None:
    page = current_page("廢棄物清理法", date(2024, 1, 1), []).replace(
        '<span class="csp-s0"></pre>',
        '<strong class="ql-font-mingliu">&nbsp;第 一 章 總則</strong>\n'
        '<strong class="ql-font-mingliu">第 1 條</strong>\n'
        '<span class="ql-font-mingliu">'
        "為有效清除、處理廢棄物，改善環境衛生，維護國民健康，特制定本法；</span>\n"
        '<span class="ql-font-mingliu">本法未規定者，適用其他有關法律之規定。</span>\n</pre>',
    )

    (article,) = moenv_law.parse_version(page).articles
    assert article.article_no == "第 1 條"
    assert article.content == (
        "為有效清除、處理廢棄物，改善環境衛生，維護國民健康，特制定本法；本法未規定者，適用其他有關法律之規定。"
    )
