from datetime import date
from typing import Any

from envcite_ontology.ingest import law as parsing
from envcite_ontology.ingest.audit import Metrics

DATA_DATE = date(2026, 9, 18)


def _law(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "LawLevel": "法律",
        "LawName": "空氣污染防制法",
        "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=O0020001",
        "LawModifiedDate": "20180801",
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": (
            "1.中華民國六十四年五月二十三日總統令制定公布全文 3 條\r\n"
            "2.中華民國一百零七年八月一日總統令修正公布全文 3 條；並自公布日施行"
        ),
        "LawArticles": [
            {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 章 總則"},
            {
                "ArticleType": "A",
                "ArticleNo": "第 1 條",
                "ArticleContent": "為防制空氣污染，特制定本法。",
            },
            {"ArticleType": "A", "ArticleNo": "第 2 條", "ArticleContent": "（刪除）"},
            {
                "ArticleType": "A",
                "ArticleNo": "第 3-1 條",
                "ArticleContent": "本法用詞如下：\r\n一、甲。\r\n二、乙。\r\n前項用詞另定之。",
            },
        ],
    }
    return base | over


def _parse(**over: Any) -> parsing.ParsedLaw:
    return parsing.parse(_law(**over), pcode="O0020001", data_date=DATA_DATE)


def test_a_current_law() -> None:
    p = _parse()
    assert p.ok and not p.problems
    assert (p.kind, p.document_type, p.title) == ("statute", "法", "空氣污染防制法")
    # 版本日取自沿革最後一次公布；生出該版本的是最後一筆事件
    assert p.version_date == date(2018, 8, 1) and p.version_event == 1
    assert [e.kind for e in p.events] == ["enactment", "full_amendment"]
    assert p.deleted_articles == 1 and p.abandoned_on is None
    assert [u.path for u in p.units] == [
        "art_1",
        "art_3-1",
        "art_3-1/para_1",
        "art_3-1/para_1/pt_1",
        "art_3-1/para_1/pt_2",
        "art_3-1/para_2",
    ]
    # 全文修正、自公布日施行：公布當日起算的第三日
    assert {(u.rule, u.starts) for u in p.units} == {("promulgation", date(2018, 8, 3))}


def test_a_repealed_law_ends_on_the_third_day_after_the_repeal() -> None:
    histories = (
        "1.中華民國六十四年五月二十三日總統令制定公布全文 3 條；並自公布日施行\r\n"
        "2.中華民國九十年一月十日總統令公布廢止"
    )
    p = _parse(LawAbandonNote="廢", LawHistories=histories, LawModifiedDate="20010110")
    assert p.ok
    assert p.version_date == date(1975, 5, 23)
    assert p.abandoned_on == date(2001, 1, 12)
    assert {(u.starts, u.ends) for u in p.units} == {(date(1975, 5, 25), date(2001, 1, 12))}


def test_the_commencement_note_defers_only_the_articles_it_names() -> None:
    p = _parse(LawEffectiveNote="第 1 條自一百十七年七月十五日施行。")
    by_path = {u.path: u for u in p.units if u.type == "article"}
    assert by_path["art_1"].rule == "unconfirmed" and by_path["art_1"].starts is None
    assert by_path["art_3-1"].rule == "promulgation"


def test_a_note_that_names_no_article_defers_the_whole_law() -> None:
    p = _parse(LawEffectiveNote="自本法施行之日施行。")
    assert {u.rule for u in p.units} == {"unconfirmed"}


def test_untouched_articles_of_a_partial_amendment_are_bounded_by_the_version_date() -> None:
    histories = (
        "1.中華民國六十四年五月二十三日總統令制定公布全文 3 條\r\n"
        "2.中華民國一百零七年八月一日總統令修正公布第 1 條條文；並自公布日施行"
    )
    p = _parse(LawHistories=histories)
    rules = {u.path: u.rule for u in p.units if u.type == "article"}
    assert rules == {"art_1": "promulgation", "art_3-1": "unchanged"}


def test_a_date_left_in_an_untouched_last_article_is_not_this_versions() -> None:
    """末條是制定時的「自某日施行」、最後一次修正沒觸及它：那是制定的施行日，修正的條待確認。"""
    histories = (
        "1.中華民國六十四年五月二十三日總統令制定公布全文 3 條\r\n"
        "2.中華民國一百零七年八月一日總統令修正公布第 1 條條文"
    )
    articles = _law()["LawArticles"]
    articles[-1] = {
        "ArticleType": "A",
        "ArticleNo": "第 3-1 條",
        "ArticleContent": "本法自中華民國六十四年七月一日施行。",
    }
    p = _parse(LawHistories=histories, LawArticles=articles)
    rules = {u.path: (u.rule, u.starts) for u in p.units if u.type == "article"}
    assert rules == {"art_1": ("unconfirmed", None), "art_3-1": ("unchanged", date(2018, 8, 1))}
    assert ("warning", "stale_last_article") in {(x.level, x.code) for x in p.problems}

    # 同一次修正也改了末條：末條的日期就是這一版的，早於公布日是明文的溯及既往
    p = _parse(LawHistories=histories.replace("第 1 條", "第 1、3-1 條"), LawArticles=articles)
    assert {u.rule for u in p.units if u.type == "article"} == {"retroactive"}


def test_a_written_date_before_the_promulgation_is_retroactive() -> None:
    histories = "1.中華民國一百零七年八月一日總統令制定公布全文 3 條；並自一百零七年一月一日施行"
    p = _parse(LawHistories=histories)
    assert {(u.rule, u.starts) for u in p.units} == {("retroactive", date(2018, 1, 1))}


def test_a_later_commencement_order_dates_the_articles_awaiting_it() -> None:
    histories = (
        "1.中華民國一百零七年八月一日總統令制定公布全文 3 條\r\n"
        "2.中華民國一百零八年一月一日行政院院臺字第 1 號令發布定自一百零八年二月一日施行"
    )
    p = _parse(
        LawHistories=histories, LawModifiedDate="20180801", LawArticles=_law()["LawArticles"]
    )
    # 最後一次公布沒有施行語句、末條也沒有：不推定
    assert {u.rule for u in p.units} == {"unconfirmed"}
    articles = _law()["LawArticles"]
    articles[-1] = {
        "ArticleType": "A",
        "ArticleNo": "第 3-1 條",
        "ArticleContent": "本法施行日期，由行政院定之。",
    }
    p = _parse(LawHistories=histories, LawArticles=articles)
    assert {(u.rule, u.starts) for u in p.units} == {("specified", date(2019, 2, 1))}


def test_problems_are_reported_per_law_without_raising() -> None:
    p = _parse(LawLevel="判例", LawName="", LawHistories="", LawModifiedDate="")
    codes = {(x.level, x.code) for x in p.problems}
    assert not p.ok
    assert {("error", "law_level"), ("error", "title"), ("error", "version_date")} <= codes


def test_duplicate_article_numbers_reject_the_law() -> None:
    articles = _law()["LawArticles"] + [
        {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "重複。"}
    ]
    p = _parse(LawArticles=articles)
    assert not p.ok and ("error", "article_duplicate") in {(x.level, x.code) for x in p.problems}


def test_an_article_that_cannot_be_split_keeps_its_whole_text() -> None:
    articles = [
        {
            "ArticleType": "A",
            "ArticleNo": "第 1 條",
            "ArticleContent": "導引：\r\n一、甲。\r\n三、乙。",
        }
    ]
    p = _parse(LawArticles=articles)
    assert p.ok and p.degraded_articles == 1
    assert [(u.path, u.content) for u in p.units] == [("art_1", "導引：\n一、甲。\n三、乙。")]


def test_a_law_without_article_text_is_kept_with_a_warning() -> None:
    p = _parse(LawArticles=[{"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第一章"}])
    assert (
        p.ok
        and not p.units
        and ("warning", "no_article_text") in {(x.level, x.code) for x in p.problems}
    )


def test_document_types() -> None:
    assert [
        parsing.document_type(n)
        for n in ("水污染防治法", "廢棄物清理法施行細則", "某某編制表", "臨時條款")
    ] == [
        "法",
        "細則",
        "編制表",
        "其他",
    ]


def test_metrics_gate_on_ratios_not_on_single_laws() -> None:
    m = Metrics()
    for _ in range(199):
        m.add(_parse())
    assert m.violations() == []
    m.add(_parse(LawLevel="判例"))  # 1/200 = 0.5%，恰在上限
    assert m.violations() == []
    m.add(_parse(LawLevel="判例"))
    assert any("不可寫入" in v for v in m.violations())
    assert Metrics().violations() == ["沒有任何法規"]


def test_a_note_without_articles_takes_the_date_its_effective_field_confirms() -> None:
    note = "一百零七年八月一日修正全文 3 條，施行日期定自一百零八年三月一日施行。"
    p = _parse(
        LawEffectiveNote=note, LawEffectiveDate="20190301", LawHistories=_law()["LawHistories"]
    )
    assert {(u.rule, u.starts) for u in p.units} == {("specified", date(2019, 3, 1))}

    p = _parse(LawEffectiveNote="施行日期，由行政院定之。", LawEffectiveDate="99991231")
    assert {(u.rule, u.starts) for u in p.units} == {("by_order", None)}


def test_a_repealed_laws_unconfirmed_articles_do_not_count_against_the_batch() -> None:
    histories = (
        "1.中華民國六十四年五月二十三日總統令制定公布全文 3 條\r\n"
        "2.中華民國九十年一月十日總統令公布廢止"
    )
    repealed = _parse(LawAbandonNote="廢", LawHistories=histories, LawModifiedDate="20010110")
    live = _parse(LawHistories="1.中華民國六十四年五月二十三日總統令制定公布全文 3 條")
    m = Metrics()
    m.add(repealed)
    assert repealed.abandoned_on is not None and m.unconfirmed_articles == 0
    m.add(live)
    assert m.unconfirmed_articles == m.live_articles > 0
