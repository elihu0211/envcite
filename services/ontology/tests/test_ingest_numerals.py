from datetime import date

import pytest

from envcite_ontology.ingest.numerals import first_date, parse_int, parse_yyyymmdd, roc_date


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("一", 1), ("十", 10), ("十二", 12), ("二十", 20), ("三十一", 31),
        ("一百", 100), ("一百零七", 107), ("一百十二", 112), ("一百二十", 120), ("一百三十", 130),
        ("一百零", 100), ("八十九", 89), ("廿五", 25), ("卅", 30), ("一〇三", 103),
        # 原文寫「一百三年」指 103 年（民國 103 年 1 月 22 日）；通用套件會讀成 130。
        ("一百三", 103),
    ],
)  # fmt: skip
def test_parse_int(text: str, value: int) -> None:
    assert parse_int(text) == value


@pytest.mark.parametrize("text", ["", "abc", "一二十", "十十", "一百三四"])
def test_parse_int_rejects_what_it_cannot_read(text: str) -> None:
    with pytest.raises(ValueError):
        parse_int(text)


def test_roc_date_converts_and_validates() -> None:
    assert roc_date("一百零七", "八", "一") == date(2018, 8, 1)
    assert roc_date("一百三", "一", "二十二") == date(2014, 1, 22)
    assert roc_date("一百零七", "十三", "一") is None  # 13 月
    assert roc_date("一百零七", "二", "三十") is None  # 2 月 30 日


def test_first_date_needs_the_era_unless_told_otherwise() -> None:
    text = "中華民國一百零七年八月一日總統令修正公布，並自一百零八年七月一日施行"
    assert first_date(text) == date(2018, 8, 1)
    assert first_date("並自一百零八年七月一日施行") is None
    assert first_date("並自一百零八年七月一日施行", era_required=False) == date(2019, 7, 1)
    assert first_date("中華民國三十六年七月訂定發布") is None  # 只有年月：不猜日


def test_parse_yyyymmdd() -> None:
    assert parse_yyyymmdd("20140122") == date(2014, 1, 22)
    assert parse_yyyymmdd("") is None
    assert parse_yyyymmdd("20140230") is None
