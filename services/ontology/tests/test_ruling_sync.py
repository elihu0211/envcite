"""違憲失效的自動取得（A5）：頁面解析、失效條款抽取、可重跑的取得流程。

頁面用固定樣本，網站以 `file://` 代替，不依賴網路。樣本 `tests/data/cons_*.html` 節錄自憲法法庭網站
（cons.judicial.gov.tw；資料開放宣告：須標示出處）。
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TypedDict

import psycopg
import pytest

from envcite_ontology.acquire import cons
from envcite_ontology.ruling import RulingError
from envcite_ontology.ruling_extract import extract, should_keep
from envcite_ontology.ruling_sync import sync
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _first, _law_v1, _law_v2, _next

Conn = psycopg.Connection[tuple[object, ...]]

DATA = Path(__file__).parent / "data"
TITLE = "噪音管制法"
TITLES = frozenset({TITLE, "刑法"})
DECIDED = date(2019, 1, 1)


def _extract(text: str, decided: date = DECIDED) -> list[tuple[str, str, str, bool]]:
    return [
        (c.law_title, c.path, c.invalid_from.isoformat(), c.partial)
        for c in extract(text, decided, TITLES)
    ]


def test_an_interpretation_page_is_read_from_its_fields() -> None:
    page = cons.parse((DATA / "cons_interpretation_733.html").read_text(encoding="utf-8"), "u")

    assert (page.ruling_no, page.kind, page.decided_on) == (
        "釋字第733號",
        "interpretation",
        date(2015, 10, 30),
    )
    assert "至遲於屆滿一年時，失其效力" in page.main_text


def test_a_judgment_page_is_read_with_its_main_text() -> None:
    page = cons.parse((DATA / "cons_judgment_114_1.html").read_text(encoding="utf-8"), "u")

    assert (page.ruling_no, page.kind) == ("114年憲判字第1號", "judgment")
    assert page.decided_on == date(2025, 12, 19)
    assert page.main_text.splitlines()[-1] == "三、本件暫時處分之聲請駁回。"


def test_a_page_that_is_not_a_ruling_is_unreadable() -> None:
    with pytest.raises(cons.UnreadablePage):
        cons.parse("<html><body>404</body></html>", "u")


def test_roc_dates() -> None:
    assert cons.roc_date("中華民國 106年05月24日 院台大二字第1060014008號") == date(2017, 5, 24)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "噪音管制法第1條第2項規定違憲，應自本解釋公布之日起失其效力。",
            [(TITLE, "art_1/para_2", "2019-01-01", False)],
        ),
        (
            "噪音管制法第五條之一，與憲法第23條規定不符，應自本判決宣示之日起，至遲於屆滿一年時，失其效力。",
            [(TITLE, "art_5-1", "2020-01-01", False)],
        ),
        (
            "噪音管制法第1條關於未滿十八歲者部分，違憲，至遲於屆滿六個月時失其效力。",
            [(TITLE, "art_1", "2019-07-01", True)],
        ),
        (
            "噪音管制法第1條違憲，自中華民國108年6月1日起失其效力。",
            [(TITLE, "art_1", "2019-06-01", False)],
        ),
        (
            "噪音管制法第一條違憲，自中華民國一百零八年六月一日起失其效力。",
            [(TITLE, "art_1", "2019-06-01", False)],
        ),
        # 引號內的條文原文與憲法條號不計。
        (
            "噪音管制法第1條規定：「依刑法第2條處罰」，與憲法第7條不符，自本解釋公布之日起失其效力。",
            [(TITLE, "art_1", "2019-01-01", False)],
        ),
    ],
)
def test_explicit_wordings_are_extracted(
    text: str, expected: list[tuple[str, str, str, bool]]
) -> None:
    assert _extract(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # 兩個條號：不猜哪個
        "噪音管制法第1條及第2條違憲，自本解釋公布之日起失其效力。",
        # 回指，沒有法規名稱
        "同法第3條違憲，自本解釋公布之日起失其效力。",
        # 附條件
        "噪音管制法第1條違憲，逾期未修法者，自屆期之日起失其效力。",
        # 日期不明
        "噪音管制法第1條違憲，失其效力。",
        # 標題不在語料
        "水污染防治法第1條違憲，自本解釋公布之日起失其效力。",
        # 沒有宣告違憲
        "噪音管制法第1條於合憲解釋下維持，失效之疑義不存在。",
    ],
)
def test_ambiguous_wordings_are_not_guessed(text: str) -> None:
    assert _extract(text) == []


def test_only_rulings_that_declare_invalidity_are_kept() -> None:
    assert should_keep("噪音管制法第1條違憲，應自本解釋公布之日起失其效力。")
    assert not should_keep("聲請不受理。")


def _page(no: str, decided: str, text: str, kind: str = "interpretation") -> str:
    if kind == "interpretation":
        head = (
            f'<li class="title">解釋字號</li><li class="text">{no}【某案】</li></ul>'
            f'<ul><li class="title">解釋公布院令</li><li class="text">中華民國 {decided}</li></ul>'
            f'<ul><li class="title">解釋文</li><li class="text"><ul class="paragraphs">'
        )
    else:
        head = (
            f'<li class="title">判決字號</li><li class="text">{no}【某案】</li></ul>'
            f'<ul><li class="title">判決日期</li><li class="text">{decided}</li></ul>'
            f'<ul><li class="title">主文</li><li class="text"><ul class="paragraphs">'
        )
    return f'<ul class="flex">{head}<li><label>1</label><pre>　　{text}</pre></li></ul></li></ul>'


SITE = "https://site.test"


class Site(TypedDict):
    fetch: Callable[[str], str]
    interpretation_list: str
    judgment_list: str
    base: str


def _site(pages: dict[int, str], judgments: dict[int, str] | None = None) -> Site:
    """記憶體裡的網站：`sync` 的 fetch 與位址參數（網址逐字對應，不連網）。"""
    judgments = judgments or {}
    served = {
        f"{SITE}/list?page=1": "".join(
            f'<a href="/docdata.aspx?fid=100&amp;id={n}">x</a>' for n in pages
        ),
        f"{SITE}/judgments": "".join(
            f'<a href="/docdata.aspx?fid=38&amp;id={n}">x</a>' for n in judgments
        ),
    }
    for fid, group in (("100", pages), ("38", judgments)):
        for number, body in group.items():
            served[f"{SITE}/docdata.aspx?fid={fid}&id={number}"] = body
    return {
        "fetch": served.__getitem__,
        "interpretation_list": f"{SITE}/list?page={{page}}",
        "judgment_list": f"{SITE}/judgments",
        "base": SITE,
    }


def _invalidations(conn: Conn) -> list[tuple[str, str, str, bool]]:
    return [
        (str(no), str(path), str(since), bool(partial))
        for no, path, since, partial in conn.execute(
            "SELECT r.ruling_no, p.path, i.invalid_from::text, i.partial"
            "  FROM corpus.ruling_invalidation AS i"
            "  JOIN corpus.constitutional_ruling AS r ON r.id = i.ruling_id"
            "  JOIN corpus.provision AS p ON p.id = i.provision_id ORDER BY 1, 2"
        )
    ]


ORDER = f"{TITLE}第1條違憲，應自本解釋公布之日起失其效力。"


def test_fetching_loads_the_ruling_and_its_invalidation(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    site = _site({1: _page("釋字第1號", "108年01月01日", ORDER)})

    result = sync(conn, pause=0, **site)

    assert (result.fetched, result.rulings, result.invalidations, result.unresolved) == (1, 1, 1, 0)
    assert _invalidations(conn) == [("釋字第1號", "art_1", "2019-01-01", False)]
    assert _scalar(conn, "SELECT count(*) FROM corpus.ruling_scan") == 1


def test_fetching_again_changes_nothing_and_does_not_fetch(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    site = _site({1: _page("釋字第1號", "108年01月01日", ORDER)})
    sync(conn, pause=0, **site)
    before = _invalidations(conn)

    again = sync(conn, pause=0, **site)

    assert (again.fetched, again.rulings, again.invalidations) == (0, 1, 1)
    assert _invalidations(conn) == before
    rescanned = sync(conn, rescan=True, pause=0, **site)
    assert (rescanned.fetched, _invalidations(conn)) == (1, before)


def test_a_ruling_the_corpus_cannot_place_waits_for_the_law(conn: Conn, tmp_path: Path) -> None:
    site = _site({1: _page("釋字第1號", "108年01月01日", ORDER)})

    first = sync(conn, pause=0, **site)
    assert (first.rulings, first.invalidations, first.unresolved) == (1, 0, 1)

    # 法規後來進了語料：不必重抓網頁，下次執行就補上。
    _first(conn, tmp_path, _law_v1())
    later = sync(conn, pause=0, **site)
    assert (later.fetched, later.invalidations, later.unresolved) == (0, 1, 0)


def test_rulings_that_declare_nothing_are_scanned_but_not_kept(conn: Conn, tmp_path: Path) -> None:
    site = _site({1: _page("釋字第2號", "108年01月01日", "聲請不受理。")})

    result = sync(conn, pause=0, **site)

    assert (result.fetched, result.rulings) == (1, 0)
    assert _scalar(conn, "SELECT count(*) FROM corpus.ruling_scan") == 1


def test_judgments_are_fetched_too(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    judgment = _page(
        "108年憲判字第1號",
        "108年01月01日",
        f"{TITLE}第1條違憲，自本判決宣示之日起失其效力。",
        kind="judgment",
    )
    site = _site({}, {7: judgment})

    sync(conn, pause=0, **site)

    assert _invalidations(conn) == [("108年憲判字第1號", "art_1", "2019-01-01", False)]


def test_a_page_that_is_not_a_ruling_is_counted_and_not_recorded(
    conn: Conn, tmp_path: Path
) -> None:
    site = _site({1: "<html>錯誤頁</html>"})

    result = sync(conn, pause=0, **site)

    assert (result.fetched, result.unreadable) == (1, 1)
    assert _scalar(conn, "SELECT count(*) FROM corpus.ruling_scan") == 0


def test_a_ruling_whose_text_changed_at_the_source_is_rejected(conn: Conn, tmp_path: Path) -> None:
    site = _site({1: _page("釋字第1號", "108年01月01日", ORDER)})
    sync(conn, pause=0, **site)
    site = _site({1: _page("釋字第1號", "108年01月01日", ORDER.replace("第1條", "第2條"))})

    with pytest.raises(RulingError, match="內容與這次不同"):
        sync(conn, rescan=True, pause=0, **site)


def test_hand_loaded_invalidations_survive_a_resync(conn: Conn, tmp_path: Path) -> None:
    from envcite_ontology.ruling import load

    _first(conn, tmp_path, _law_v1())
    _next(conn, tmp_path, _law_v2())
    text = "甲法第一條自本解釋公布之日起失其效力。"
    page = _page("釋字第1號", "108年01月01日", text)
    site = _site({1: page})
    sync(conn, pause=0, **site)
    load(
        conn,
        {
            "rulings": [
                {
                    "ruling_no": "釋字第9號",
                    "kind": "interpretation",
                    "decided_on": "2019-01-01",
                    "source_url": "https://cons.judicial.gov.tw/x",
                    "excerpt": "人工填載",
                    "invalidates": [{"law": LAW, "path": "art_2", "invalid_from": "2020-01-01"}],
                }
            ]
        },
    )

    sync(conn, rescan=True, pause=0, **site)

    assert ("釋字第9號", "art_2", "2020-01-01", False) in _invalidations(conn)
