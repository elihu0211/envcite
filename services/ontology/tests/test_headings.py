"""結構章節（編、章、節、款、目）抽取與交錯 sort_key 測試。"""

from datetime import date

import psycopg

from envcite_ontology.ingest import articles, headings, law, write
from envcite_ontology.store import LawKey, OntologyStore


def test_extract_builds_hierarchy_and_interleaved_sort_keys() -> None:
    raw = [
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 編 總則"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 章 法例"},
        {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "第一條內容。"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 節 定義"},
        {"ArticleType": "A", "ArticleNo": "第 2 條", "ArticleContent": "（刪除）"},
        {"ArticleType": "A", "ArticleNo": "第 2-1 條", "ArticleContent": "第二條之一內容。"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 二 章 監督"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 款 申報"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "   第 一 目 項目"},
        {"ArticleType": "A", "ArticleNo": "第 3 條", "ArticleContent": "第三條內容。"},
    ]

    extracted = headings.extract(raw)

    assert [(h.kind, h.heading_no, h.title, h.parent_index) for h in extracted] == [
        ("part", "第一編", "總則", None),
        ("chapter", "第一章", "法例", 0),
        ("section", "第一節", "定義", 1),
        ("chapter", "第二章", "監督", 0),
        ("subsection", "第一款", "申報", 3),
        ("item_heading", "第一目", "項目", 4),
    ]

    art_1 = articles.sort_key("art_1")
    art_2_1 = articles.sort_key("art_2-1")
    art_3 = articles.sort_key("art_3")

    assert extracted[0].sort_key < extracted[1].sort_key < art_1
    assert art_1 < extracted[2].sort_key < art_2_1
    assert art_2_1 < extracted[3].sort_key < extracted[4].sort_key < extracted[5].sort_key < art_3


def test_law_parse_populates_headings() -> None:
    payload = {
        "LawLevel": "法律",
        "LawName": "測試法",
        "LawModifiedDate": "20240101",
        "LawHistories": "1.中華民國一百十三年一月一日總統制定公布全文 1 條；並自公布日施行",
        "LawArticles": [
            {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 一 章 總則"},
            {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "本法為測試用。"},
        ],
    }

    parsed = law.parse(payload, pcode="O0010001", data_date=date(2024, 1, 2))

    assert parsed.ok
    assert len(parsed.headings) == 1
    assert parsed.headings[0].kind == "chapter"
    assert parsed.headings[0].heading_no == "第一章"
    assert parsed.headings[0].title == "總則"


def test_write_and_list_headings_round_trip(
    conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore
) -> None:
    payload = {
        "LawLevel": "法律",
        "LawName": "結構章節測試法",
        "LawModifiedDate": "20240101",
        "LawHistories": "1.中華民國一百十三年一月一日總統制定公布全文 2 條；並自公布日施行",
        "LawArticles": [
            {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 一 編 總則"},
            {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 一 章 通則"},
            {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "第一條。"},
            {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 二 章 附則"},
            {"ArticleType": "A", "ArticleNo": "第 2 條", "ArticleContent": "第二條。"},
        ],
    }
    parsed = law.parse(payload, pcode="O0099001", data_date=date(2024, 1, 2))
    assert parsed.ok

    # 經 `conn` fixture 寫入，測試結束時它才會清掉這部法規；自己另開連線會留給之後的測試。
    with conn.transaction():
        write.apply(conn, parsed, first_import=True)

    key = LawKey("TW-CENTRAL", "moj_pcode", "O0099001")
    listed_headings = store.list_headings(key, as_of="2024-06-01")
    listed_articles = store.list_articles(key, as_of="2024-06-01")

    assert listed_headings is not None
    assert listed_articles is not None
    assert [(h.kind, h.heading_no, h.title, h.parent_index) for h in listed_headings] == [
        ("part", "第一編", "總則", -1),
        ("chapter", "第一章", "通則", 0),
        ("chapter", "第二章", "附則", 0),
    ]

    interleaved = sorted(
        [(h.sort_key, f"{h.heading_no} {h.title}") for h in listed_headings]
        + [(a.sort_key, f"第 {a.number} 條") for a in listed_articles],
        key=lambda item: item[0],
    )
    assert [label for _, label in interleaved] == [
        "第一編 總則",
        "第一章 通則",
        "第 1 條",
        "第二章 附則",
        "第 2 條",
    ]
