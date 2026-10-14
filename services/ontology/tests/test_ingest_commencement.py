from datetime import date

import pytest

from envcite_ontology.ingest.commencement import Commencement, derive, from_note, touched_articles

KNOWN = ["art_1", "art_2", "art_2-1", "art_3", "art_39", "art_39-1", "art_39-5", "art_40"]


def test_touched_lists_and_ranges() -> None:
    entry = (
        "中華民國一百零六年一月十八日總統令修正公布第 2、14、28  條條文；並增訂第 2-1、39-1 條條文"
    )
    assert touched_articles(entry, KNOWN) == {"art_2", "art_14", "art_28", "art_2-1", "art_39-1"}
    assert touched_articles("修正公布第 39～39-5 條條文", KNOWN) == {
        "art_39",
        "art_39-1",
        "art_39-5",
    }
    assert touched_articles("修正公布第 1 條至第 3 條", KNOWN) >= {
        "art_1",
        "art_2",
        "art_2-1",
        "art_3",
    }
    assert touched_articles("修正公布全文 28 條", KNOWN) == set()


V = date(2024, 5, 1)


def d(**kw: object) -> Commencement:
    args: dict[str, object] = {
        "version_date": V,
        "entry": "",
        "last_article": "",
        "deferred": False,
        "touched": True,
    }
    args.update(kw)
    return derive(**args)  # type: ignore[arg-type]


def test_a_commencement_note_makes_the_whole_law_unconfirmed() -> None:
    assert d(deferred=True) == Commencement("unconfirmed", None)
    assert d(deferred=True, touched=False) == Commencement("unconfirmed", None)


def test_untouched_articles_are_bounded_by_the_version_date() -> None:
    assert d(touched=False) == Commencement("unchanged", V)


def test_promulgation_day_takes_effect_on_the_third_day() -> None:
    got = d(entry="總統令修正公布全文 10 條；並自公布日施行")
    assert got == Commencement("promulgation", date(2024, 5, 3))


def test_last_article_is_consulted_when_the_entry_is_silent() -> None:
    assert d(entry="修正公布第 5 條條文", last_article="本法自公布日施行。") == Commencement(
        "promulgation", date(2024, 5, 3)
    )
    assert d(entry="修正公布第 5 條條文", last_article="本辦法自發布日施行。") == Commencement(
        "promulgation", date(2024, 5, 3)
    )


def test_a_specific_date_is_used_as_written() -> None:
    got = d(entry="令訂定發布全文 34 條；並自一百零四年五月三日施行")
    assert got == Commencement("specified", date(2015, 5, 3))
    got = d(entry="修正發布第 5 條條文", last_article="本辦法自中華民國一百十三年七月一日施行。")
    assert got == Commencement("specified", date(2024, 7, 1))


def test_per_article_dates_are_not_law_wide() -> None:
    entry = "修正公布第 46、77 條條文；依第 77 條規定：第 46 條自九十五年七月一日施行"
    assert d(entry=entry) == Commencement("unconfirmed", None)


def test_exceptions_and_relative_dates_are_unconfirmed() -> None:
    assert d(entry="訂定發布全文 23 條；除另定施行日期者外，自發布日施行") == Commencement(
        "unconfirmed", None
    )
    assert d(entry="修正發布；並自同年七月一日施行") == Commencement("unconfirmed", None)


def test_by_order() -> None:
    assert d(last_article="本法施行日期，由行政院以命令定之。") == Commencement("by_order", None)


@pytest.mark.parametrize("last_article", ["", "附表如下。"])
def test_no_statement_is_not_presumed(last_article: str) -> None:
    assert d(entry="修正公布第 5 條條文", last_article=last_article) == Commencement(
        "unconfirmed", None
    )


NOTE_V = date(2024, 8, 7)


@pytest.mark.parametrize(
    ("note", "effective", "expected"),
    [
        # 施行日欄與說明裡的日期吻合：採用。
        (
            "一百十三年八月七日修正全文 79 條，施行日期定自一百十四年八月六日施行。",
            date(2025, 8, 6),
            Commencement("specified", date(2025, 8, 6)),
        ),
        # 欄位為 9999-12-31：由命令定之。
        ("施行日期，由行政院定之。", date(9999, 12, 31), Commencement("by_order", None)),
        # 欄位與說明的日期對不上：不猜。
        (
            "一百十二年十二月二十一日修正全文 30 條，自一百十三年一月三十一日施行。",
            date(2024, 1, 30),
            Commencement("unconfirmed", None),
        ),
        # 相對表達（某法施行之日）：說明沒有日期可印證。
        ("自陸海空軍懲罰法施行之日施行。", date(2025, 8, 6), Commencement("unconfirmed", None)),
        # 「除…者外」：其餘條的日期不明。
        (
            "修正全文 9 條；除另定施行日期者外，自一百十四年八月六日施行。",
            date(2025, 8, 6),
            Commencement("unconfirmed", None),
        ),
        # 沒有施行日欄。
        ("自一百十四年八月六日施行。", None, Commencement("unconfirmed", None)),
    ],
)
def test_a_note_without_articles_is_confirmed_by_the_effective_date_field(
    note: str, effective: date | None, expected: Commencement
) -> None:
    assert from_note(note, effective, NOTE_V) == expected


def test_the_note_date_applies_to_touched_articles_only() -> None:
    note = Commencement("specified", date(2025, 8, 6))
    assert d(deferred=False, note=note) == note
    assert d(deferred=False, touched=False, note=note) == Commencement("unchanged", V)
    assert d(deferred=True, note=note) == Commencement("unconfirmed", None)
