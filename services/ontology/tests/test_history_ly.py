"""法律的歷史版本取自立法院法律系統：經 `envcite-history ly` 的入口，對真實綱要。

情境同 `test_backfill`（一部三版的法律，全國法規資料庫只有 109 年版），立法院的各版另有三讀日。
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.range import Range

from envcite_jobkit import Policy
from envcite_ontology import history_sync
from envcite_ontology.acquire import ly_law
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.history_sources import Ly, Moenv
from envcite_ontology.ingest import importer
from tests import ly_site, moenv_site
from tests.fake_web import FakeWeb
from tests.sample import stored_snapshot
from tests.test_import import _law

Conn = psycopg.Connection[tuple[object, ...]]

PCODE = "T0000001"
TITLE = "噪音管制法"
HISTORY = (
    "1.中華民國八十年五月二十三日總統令制定公布全文 4 條\r\n"
    "2.中華民國一百零七年八月一日總統令修正公布全文 3 條；並自公布日施行\r\n"
    "3.中華民國一百零九年三月一日總統令修正公布第 2 條條文；並自公布日施行"
)
V1, V2, V3 = date(1991, 5, 23), date(2018, 8, 1), date(2020, 3, 1)
PASSED = {V1: date(1991, 5, 3), V2: date(2018, 7, 10), V3: date(2020, 2, 11)}
DAY3 = timedelta(days=2)

V1_TEXT = [(1, "甲。"), (2, "乙。"), (3, "丙。"), (4, "本法自公布日施行。")]
V2_TEXT = [(1, "甲。"), (2, "丙。"), (3, "本法自公布日施行。")]
V3_TEXT = [(1, "甲。"), (2, "丁。"), (3, "本法自公布日施行。")]

POLICY = Policy(name="test", max_attempts=1, backoff_base_seconds=0.0)


@pytest.fixture(autouse=True)
def _no_batch_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """一部法規就是一整批：任何比例都是 0 或 100%，沒有意義。"""
    monkeypatch.setattr(
        history_sync, "LIMITS", {k: (d, 1.0) for k, (d, _) in history_sync.LIMITS.items()}
    )
    monkeypatch.setattr(Ly, "unmatched_limit", 1.0)


def _anchor(conn: Conn, root: Path) -> None:
    articles = [
        {"ArticleType": "A", "ArticleNo": f"第 {n} 條", "ArticleContent": t} for n, t in V3_TEXT
    ]
    stored_snapshot(
        conn,
        root,
        [
            _law(
                PCODE, TITLE, LawHistories=HISTORY, LawModifiedDate="20200301", LawArticles=articles
            )
        ],
    )
    importer.run(conn, root, Moj())
    conn.commit()


def _web(**over: Any) -> FakeWeb:
    versions = over.pop("versions", [(V1, V1_TEXT), (V2, V2_TEXT), (V3, V3_TEXT)])
    pages, forms = ly_site.site(TITLE, [(PASSED[d], d, t) for d, t in versions], **over)
    return FakeWeb(pages, forms)


def _sync(conn: Conn, root: Path, web: FakeWeb, source: Any = None) -> int:
    return history_sync.sync(conn, source or Ly(), root, web, policy=POLICY, sleep=lambda _: None)


def _expressions(conn: Conn) -> list[tuple[date, Range[date], str]]:
    return conn.execute(  # type: ignore[return-value]
        "SELECT version_date, text_period, text_authority::text FROM corpus.legal_expression"
        " ORDER BY version_date"
    ).fetchall()


def _article(conn: Conn, path: str, on: date) -> str | None:
    row = conn.execute(
        "SELECT corpus.article_text(a.version_id)"
        "  FROM corpus.legal_resource AS r, LATERAL corpus.provisions_at(r.id, 'zho', %s) AS a"
        " WHERE r.local_id = %s AND a.path = %s",
        (on, PCODE, path),
    ).fetchone()
    return None if row is None else row[0]  # type: ignore[return-value]


def test_past_versions_are_dated_by_promulgation_and_chained_to_the_anchor(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    assert _sync(conn, tmp_path, _web()) == 0

    assert _expressions(conn) == [
        (V1, Range(V1, V2, "[)"), "legislature"),
        (V2, Range(V2, V3, "[)"), "legislature"),
        (V3, Range(V3, None, "[)"), "official_db"),
    ]
    assert _article(conn, "art_2", date(2000, 1, 1)) == "乙。"
    assert _article(conn, "art_2", date(2019, 1, 1)) == "丙。"  # 條號重排
    assert _article(conn, "art_2", V3 + DAY3) == "丁。"
    snapshot = conn.execute(
        "SELECT license_notice FROM source.snapshot WHERE feed = 'ly_law'"
    ).fetchone()
    assert snapshot == (ly_law.ATTRIBUTION,)


def test_a_rerun_in_a_new_session_fetches_only_the_search_and_the_law_page(
    conn: Conn, tmp_path: Path
) -> None:
    """連結的代碼每個連線階段不同；過去的版本以法律編號與通過日沿用，不重抓。"""
    _anchor(conn, tmp_path)
    _sync(conn, tmp_path, _web())
    before = _expressions(conn)

    web = _web(session="7F7F7F")
    assert _sync(conn, tmp_path, web) == 0

    assert len(web.requested) == 3  # 首頁、檢索、法律頁
    assert _expressions(conn) == before
    assert conn.execute(
        "SELECT count(*) FROM source.snapshot WHERE feed = 'ly_law'"
    ).fetchone() == (1,)


def test_a_law_not_found_by_name_is_unmatched_and_not_written(conn: Conn, tmp_path: Path) -> None:
    _anchor(conn, tmp_path)
    web = _web()
    web.forms = {k: ly_site.results([("噪音管制法施行細則", V1, V1)]) for k in web.forms}

    assert _sync(conn, tmp_path, web) == 0

    assert [d for d, _, _ in _expressions(conn)] == [V3]
    problems = conn.execute("SELECT severity, problem_code FROM source.import_problem").fetchall()
    assert problems == [("error", "unmatched")]


def test_the_legislature_takes_over_a_statute_written_from_the_competent_authority(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """法律的歷史只取自立法院：主管機關系統寫過的同日版本改記立法院，文字依立法院的。"""
    _anchor(conn, tmp_path)
    monkeypatch.setattr(Moenv, "KINDS", ("法律",))
    moenv_pages = moenv_site.site(
        "FL000001",
        TITLE,
        (V3, [(str(n), t) for n, t in V3_TEXT]),
        {12: (V2, [(str(n), t) for n, t in V2_TEXT]), 11: (V1, [("1", "甲 舊。")])},
        kind="法律",
    )
    monkeypatch.setattr(
        history_sync, "LIMITS", {k: (d, 1.0) for k, (d, _) in history_sync.LIMITS.items()}
    )
    monkeypatch.setattr(Moenv, "unmatched_limit", 1.0)
    assert _sync(conn, tmp_path, FakeWeb(moenv_pages), Moenv()) == 0
    assert [a for _, _, a in _expressions(conn)][:2] == ["competent_authority"] * 2

    assert _sync(conn, tmp_path, _web()) == 0

    assert [a for _, _, a in _expressions(conn)] == ["legislature", "legislature", "official_db"]
    assert _article(conn, "art_1", date(2000, 1, 1)) == "甲。"


def test_a_snapshot_of_a_statute_left_by_the_competent_authority_is_skipped(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """主管機關系統只收命令；以前取得的法律快照不再匯入，也不記問題。"""
    _anchor(conn, tmp_path)
    monkeypatch.setattr(Moenv, "unmatched_limit", 1.0)
    pages = moenv_site.site(
        "FL000001", TITLE, (V3, [(str(n), t) for n, t in V3_TEXT]), {}, kind="法律"
    )
    with monkeypatch.context() as m:
        m.setattr(Moenv, "KINDS", ("法律",))
        assert _sync(conn, tmp_path, FakeWeb(pages), Moenv()) == 0
    conn.execute("DELETE FROM source.import_problem")
    conn.commit()

    assert _sync(conn, tmp_path, FakeWeb(pages), Moenv()) == 0

    assert conn.execute("SELECT count(*) FROM source.import_problem").fetchone() == (0,)


def test_versions_promulgated_on_the_same_day_take_the_later_passed(
    conn: Conn, tmp_path: Path
) -> None:
    """分別三讀、同日公布的兩版是累積的：後通過的一版已含先通過者的修正。"""
    _anchor(conn, tmp_path)
    first = [(1, "甲。"), (2, "戊。"), (3, "本法自公布日施行。")]
    pages, forms = ly_site.site(
        TITLE,
        [
            (PASSED[V1], V1, V1_TEXT),
            (date(2018, 7, 1), V2, first),
            (PASSED[V2], V2, V2_TEXT),
            (PASSED[V3], V3, V3_TEXT),
        ],
    )
    assert _sync(conn, tmp_path, FakeWeb(pages, forms)) == 0

    assert _article(conn, "art_2", date(2019, 1, 1)) == "丙。"
