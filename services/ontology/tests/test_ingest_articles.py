import pytest

from env_law_ontology.ingest.articles import (
    Unit,
    article_path,
    is_deleted,
    sort_key,
    split,
)


@pytest.mark.parametrize(
    ("no", "path"),
    [
        ("第 1 條", "art_1"),
        ("第 2-1 條", "art_2-1"),
        ("第 39-5 條", "art_39-5"),
        ("第 007 條", "art_7"),
        ("12", "art_12"),
        ("1-1", "art_1-1"),
    ],
)
def test_article_path(no: str, path: str) -> None:
    assert article_path(no) == path


@pytest.mark.parametrize("no", ["", "第 一 條", "附則", "第 條", "第 1 章"])
def test_article_path_unrecognised(no: str) -> None:
    assert article_path(no) is None


def test_sort_key_reading_order() -> None:
    paths = ["art_5", "art_5/para_1", "art_5/para_2/pt_1", "art_5-1", "art_5-1/para_1", "art_6"]
    assert sorted(paths, key=sort_key) == paths
    assert sort_key("art_2/para_1") == [2, 0, 1]
    assert sort_key("art_2/para_1/pt_3/item_2/sub_4") == [2, 0, 1, 0, 3, 0, 2, 0, 4]


def test_is_deleted() -> None:
    assert is_deleted("（刪除）") and is_deleted(" (刪除) ")
    assert not is_deleted("（刪除）本條")


def test_single_paragraph_is_the_article_itself() -> None:
    r = split("art_1", "為有效清除、處理廢棄物，特制定本法。")
    assert r.units == [Unit("art_1", "article", "為有效清除、處理廢棄物，特制定本法。")]
    assert r.degraded is None


def test_paragraphs_subparagraphs_and_items() -> None:
    text = (
        "本法所稱廢棄物，指下列物質：\r\n"
        "一、被拋棄者。\r\n"
        "二、事業廢棄物：\r\n"
        "（一）有害事業廢棄物。\r\n"
        "（二）一般事業廢棄物。\r\n"
        "前項認定標準，由中央主管機關定之。"
    )
    r = split("art_2", text)
    assert r.degraded is None
    assert [(u.path, u.type) for u in r.units] == [
        ("art_2", "article"),
        ("art_2/para_1", "paragraph"),
        ("art_2/para_1/pt_1", "subparagraph"),
        ("art_2/para_1/pt_2", "subparagraph"),
        ("art_2/para_1/pt_2/item_1", "item"),
        ("art_2/para_1/pt_2/item_2", "item"),
        ("art_2/para_2", "paragraph"),
    ]
    by_path = {u.path: u.content for u in r.units}
    assert by_path["art_2"] is None
    assert by_path["art_2/para_1"] == "本法所稱廢棄物，指下列物質："
    assert by_path["art_2/para_1/pt_2/item_1"] == "（一）有害事業廢棄物。"
    assert by_path["art_2/para_2"] == "前項認定標準，由中央主管機關定之。"


def test_subitem_and_numbering_reset_per_paragraph() -> None:
    text = "甲：\n一、A\n（一）B\n1.C\n2.D\n乙：\n一、E"
    r = split("art_3", text)
    assert r.degraded is None
    paths = [u.path for u in r.units]
    assert "art_3/para_1/pt_1/item_1/sub_2" in paths
    assert "art_3/para_2/pt_1" in paths


def test_article_starting_with_subparagraph_has_an_empty_first_paragraph() -> None:
    r = split("art_4", "一、甲\n二、乙")
    assert r.degraded is None
    assert r.units[1] == Unit("art_4/para_1", "paragraph", None)
    assert r.units[2].content == "一、甲"


def test_indented_lines_and_table_frames_continue_the_previous_unit() -> None:
    r = split("art_5", "前段\n    後段\n│ 甲 │ 乙 │")
    assert r.units == [Unit("art_5", "article", "前段\n後段\n│ 甲 │ 乙 │")]


def test_other_numbering_and_decimals_are_not_cut() -> None:
    r = split("art_6", "本條如下：\n(1) 甲\n1.5 公尺以上")
    assert r.degraded is None
    assert [u.path for u in r.units] == ["art_6", "art_6/para_1", "art_6/para_2", "art_6/para_3"]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("導引：\n一、甲\n三、乙", "款序位不連續"),
        ("導引：\n二、甲", "款序位不連續"),
        ("導引：\n（一）甲", "目出現在沒有上層"),
        ("導引：\n一、甲\n1.乙", "目之 1出現在沒有上層"),
    ],
)
def test_inconsistent_numbering_degrades_to_the_whole_article(text: str, why: str) -> None:
    r = split("art_7", text)
    assert r.degraded is not None and why in r.degraded
    assert r.units == [Unit("art_7", "article", text)]


def test_empty_text_degrades() -> None:
    r = split("art_8", "  \r\n")
    assert r.degraded == "沒有文字"
    assert r.units == [Unit("art_8", "article", None)]
