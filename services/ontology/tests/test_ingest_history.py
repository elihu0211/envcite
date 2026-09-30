from datetime import date

import pytest

from env_law_ontology.ingest.history import classify, entries, parse

WASTE = (
    "1.中華民國六十三年七月二十六日總統（63）台統（一）義字第 3300 號\r\n"
    "  令制定公布全文 28 條\r\n"
    "2.中華民國六十九年四月九日總統（69）台統（一）義字第 1968 號令修\r\n"
    "  正公布第 5、6、11、15、18、21 條條文；並增訂第 24-1 條條文\r\n"
    "6.中華民國八十八年七月十四日總統（88）華總（一）義字第 880015981\r\n"
    "  0 號令修正公布第 5、13、17、22、34  條條文；並增訂第 34-1 條條\r\n"
    "  文\r\n"
    "10. 中華民國九十五年五月三十日總統華總一義字第 09500075791  號令\r\n"
    "    修正公布第 46、77 條條文；依第 77 條規定：第 46 條自九十五年\r\n"
    "    七月一日施行\r\n"
    "    中華民國一百十二年八月十八日行政院院臺規字第 1121028221 號公\r\n"
    "    告第 2  條第 1  項第 5  款，自一百十二年八月十八日起改由「環境部」管轄\r\n"
)


def test_entries_rejoin_wrapped_lines_including_split_numbers() -> None:
    got = entries(WASTE)
    assert len(got) == 5
    assert got[0].endswith("令制定公布全文28條")
    assert "字第8800159810號令修正公布" in got[2]
    assert got[3].endswith("第46條自九十五年七月一日施行")
    assert got[4].startswith("中華民國一百十二年八月十八日行政院")


def test_parse_dates_kinds_and_document_numbers() -> None:
    events = parse(WASTE)
    # 管轄變更公告不是事件
    assert [e.kind for e in events] == [
        "enactment",
        "partial_amendment",
        "partial_amendment",
        "partial_amendment",
    ]
    assert events[0].date == date(1974, 7, 26)
    assert events[0].document_no == "總統（63）台統（一）義字第3300號"
    assert events[2].document_no == "總統（88）華總（一）義字第8800159810號"
    assert events[3].date == date(2006, 5, 30)


def test_only_the_leading_date_is_the_event_date() -> None:
    (e,) = parse("中華民國九十五年五月三十日總統令修正公布第 46 條條文；並自九十五年七月一日施行")
    assert e.date == date(2006, 5, 30)


def test_incomplete_date_is_kept_as_none() -> None:
    (e,) = parse("中華民國三十二年七月糧食部訂定發布")
    assert e.date is None and e.kind == "enactment"


def test_numbering_only_entry_is_dropped() -> None:
    events = parse("1.\r\n2.中華民國九十二年五月二十八日總統華總一義字第 9 號令\r\n  公布廢止")
    assert [e.kind for e in events] == ["repeal"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("中華民國九十年一日總統令制定公布全文 28 條", "enactment"),
        (
            "中華民國九十三年十月八日教育部台參字第 1 號令訂定發布全文 10 條；並自發布日施行",
            "enactment",
        ),
        ("中華民國一百年一月一日總統令修正公布全文 77 條；並自公布日施行", "full_amendment"),
        ("中華民國一百年一月一日行政院令修正發布全文 5 條", "full_amendment"),
        (
            "中華民國一百年一月一日總統令修正公布第 2 條條文；並增訂第 2-1 條條文",
            "partial_amendment",
        ),
        ("中華民國一百年一月一日總統令增訂公布第 50-1 條條文", "partial_amendment"),
        ("中華民國一百年一月一日經濟部修正發布", "partial_amendment"),
        ("中華民國九十二年一月二十二日經濟部經貿字第 1 號令發布廢止", "repeal"),
        ("中華民國九十四年十二月三十一日廢止", "repeal"),
        ("中華民國一百年一月一日總統令修正公布第 3 條條文；並廢止第 4 條條文", "partial_amendment"),
        ("中華民國一百年一月一日行政院令廢止第 4 條條文", "unknown"),
        (
            "中華民國九十八年十二月三日行政院院臺秘字第 1 號令發布定自九十六年七月六日施行",
            "commencement",
        ),
        ("中華民國九十一年二月二十七日行政院令發布自九十一年三月一日施行", "commencement"),
        (
            "中華民國一百十三年二月二十三日海洋委員會海授保字第 1 號書函勘誤第 9 條條文",
            "correction",
        ),
        ("中華民國一百年一月一日司法院院台廳民一字第 1 號函准予備查停止適用", "suspension"),
        ("中華民國九十年六月一日行政院台防字第 1 號函核定", "unknown"),
        (
            "中華民國一百一十一年五月十日交通部交航字第 1 號令定自一百十一年七月一日施行",
            "commencement",
        ),
        (
            "中華民國一百零二年十一月十四日經濟部令發布除第 8 條外，其餘自同日施行",
            "commencement",
        ),
        ("中華民國三十六年十二月二十五日施行", "commencement"),
        ("中華民國一百零三年一月二十二日生效", "commencement"),
        (
            "中華民國一百零五年十二月二十八日交通部令發布第224-1條條文，自一百零六年九月八日施行",
            "partial_amendment",
        ),
        ("中華民國六十五年二月十六日內政部令發布第24～26條條文停用", "suspension"),
        ("中華民國一百零二年七月二十三日公告第 4 條權責事項改由衛生福利部管轄", None),
    ],
)
def test_classify(text: str, kind: str | None) -> None:
    assert classify(text) == kind


def test_the_first_entry_with_an_unspecified_promulgation_is_the_enactment() -> None:
    first, later = parse(
        "1.中華民國二十九年六月二十九日國民政府公布施行\r\n2.中華民國三十年六月二十九日國民政府公布"
    )
    # 沿革的第一筆之前沒有別的公布；之後的同樣寫法不能這樣推定。
    assert first.kind == "enactment" and later.kind == "unknown"
