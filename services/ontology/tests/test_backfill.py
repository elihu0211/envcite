"""歷史版本的回推與寫入（02-02 §3）：經 `envcite-history moenv` 的入口，對真實綱要。

一部三版的命令：民國 80 年制定（4 條，自公布日施行）、107 年全文修正（條號重排：舊第 3 條
成為新第 2 條，第 4 條刪除）、109 年修正第 2 條。
全國法規資料庫只有 109 年版（錨點），主管機關系統有前兩版的全文。
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.types.range import Range

from envcite_jobkit import Policy
from envcite_ontology.acquire import moenv_law
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.history_sources import Moenv
from envcite_ontology.history_sync.run import sync
from envcite_ontology.ingest import audit, importer
from envcite_ontology.ingest.backfill import planner
from envcite_ontology.ingest.backfill.models import Anchor, Article, Version
from tests.fake_web import FakeWeb
from tests.history_support import relax_limits
from tests.moenv_site import current_page, site
from tests.sample import stored_snapshot
from tests.test_import import _law
from tests.test_incremental import _next

Conn = psycopg.Connection[tuple[object, ...]]

PCODE = "T0000001"
TITLE = "噪音管制法施行細則"
HISTORY = (
    "1.中華民國八十年五月二十三日總統令制定公布全文 4 條\r\n"
    "2.中華民國一百零七年八月一日總統令修正公布全文 3 條；並自公布日施行\r\n"
    "3.中華民國一百零九年三月一日總統令修正公布第 2 條條文；並自公布日施行"
)
V1 = date(1991, 5, 23)
V2 = date(2018, 8, 1)
V3 = date(2020, 3, 1)
DAY3 = timedelta(days=2)  # 中央法規標準法第 13 條：自公布之日起算至第三日

V1_TEXT = [("1", "甲。"), ("2", "乙。"), ("3", "丙。"), ("4", "本法自公布日施行。")]
V2_TEXT = [("1", "甲。"), ("2", "丙。"), ("3", "本法自公布日施行。")]
V3_TEXT = [("1", "甲。"), ("2", "丁。"), ("3", "本法自公布日施行。")]

POLICY = Policy(name="test", max_attempts=1, backoff_base_seconds=0.0)


@pytest.fixture(autouse=True)
def _no_batch_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """一部法規就是一整批：任何比例都是 0 或 100%，沒有意義。"""
    monkeypatch.setattr(audit, "LIMITS", {k: (d, 1.0) for k, (d, _) in audit.LIMITS.items()})
    relax_limits(monkeypatch)


def _anchor(conn: Conn, root: Path) -> None:
    articles = [
        {"ArticleType": "A", "ArticleNo": f"第 {n} 條", "ArticleContent": t} for n, t in V3_TEXT
    ]
    stored_snapshot(
        conn,
        root,
        [
            _law(
                PCODE,
                TITLE,
                LawLevel="命令",
                LawHistories=HISTORY,
                LawModifiedDate="20200301",
                LawArticles=articles,
            )
        ],
    )
    importer.run(conn, root, Moj())
    conn.commit()


def _sync(conn: Conn, root: Path, pages: dict[str, str], **kw: Any) -> int:
    return sync(conn, Moenv(), root, FakeWeb(pages), policy=POLICY, sleep=lambda _: None, **kw)


def _full_site(**over: Any) -> dict[str, str]:
    past = {12: (V2, V2_TEXT), 11: (V1, V1_TEXT)} | over.pop("past", {})
    return site("FL000001", TITLE, over.pop("current", (V3, V3_TEXT)), past)


def _rows(conn: Conn) -> list[tuple[str, date, Range[date] | None, str]]:
    return [
        (str(path), d, force, str(content))  # type: ignore[misc]
        for path, d, force, content in conn.execute(
            "SELECT p.path, e.version_date, v.force_period, corpus.article_text(v.id)"
            "  FROM corpus.provision_version AS v"
            "  JOIN corpus.provision AS p ON p.id = v.provision_id"
            "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
            " WHERE v.superseded_at IS NULL AND p.provision_type = 'article'"
            " ORDER BY p.path, e.version_date"
        )
    ]


def _at(conn: Conn, on: date) -> dict[str, tuple[str, str | None]]:
    """查詢日各條的狀態與文字（`corpus.provisions_at`）。"""
    return {
        str(path): (str(status), text)  # type: ignore[misc]
        for path, status, text in conn.execute(
            "SELECT a.path, a.status::text, corpus.article_text(a.version_id)"
            "  FROM corpus.legal_resource AS r,"
            "       LATERAL corpus.provisions_at(r.id, 'zho', %s) AS a"
            " WHERE r.local_id = %s AND a.provision_type = 'article'",
            (on, PCODE),
        )
    }


def test_past_versions_are_chained_to_the_anchor_without_gaps_or_overlaps(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    assert _sync(conn, tmp_path, _full_site()) == 0

    rows = _rows(conn)
    assert [(p, d, f.lower, f.upper) for p, d, f, _ in rows if f is not None] == [
        ("art_1", V1, V1 + DAY3, V2 + DAY3),
        ("art_1", V2, V2 + DAY3, V3),  # 109 年未修正第 1 條：錨點自版本日起
        ("art_1", V3, V3, None),
        ("art_2", V1, V1 + DAY3, V2 + DAY3),
        ("art_2", V2, V2 + DAY3, V3 + DAY3),
        ("art_2", V3, V3 + DAY3, None),
        ("art_3", V1, V1 + DAY3, V2 + DAY3),
        ("art_3", V2, V2 + DAY3, V3),
        ("art_3", V3, V3, None),
        ("art_4", V1, V1 + DAY3, V2 + DAY3),  # 107 年刪除：止於該版的法規層起點
    ]
    periods = conn.execute(
        "SELECT version_date, text_period, text_authority::text FROM corpus.legal_expression"
        " ORDER BY version_date"
    ).fetchall()
    assert periods == [
        (V1, Range(V1, V2, "[)"), "competent_authority"),
        (V2, Range(V2, V3, "[)"), "competent_authority"),
        (V3, Range(V3, None, "[)"), "official_db"),
    ]


def test_each_date_reads_the_text_then_in_force(conn: Conn, tmp_path: Path) -> None:
    _anchor(conn, tmp_path)
    _sync(conn, tmp_path, _full_site())

    assert _at(conn, date(1990, 1, 1)) == {}  # 制定之前：查無
    assert _at(conn, date(2000, 1, 1)) == {
        "art_1": ("in_force", "甲。"),
        "art_2": ("in_force", "乙。"),
        "art_3": ("in_force", "丙。"),
        "art_4": ("in_force", "本法自公布日施行。"),
    }
    # 條號重排：第 2 條是舊第 3 條的文字；第 4 條已刪除。
    assert _at(conn, date(2019, 1, 1))["art_2"] == ("in_force", "丙。")
    assert _at(conn, date(2019, 1, 1))["art_4"][0] == "no_longer_in_force"
    # 109 年修正公布當日：第 2 條尚未生效，仍是修正前的文字。
    assert _at(conn, V3 + timedelta(days=1))["art_2"] == ("in_force", "丙。")
    assert _at(conn, V3 + DAY3)["art_2"] == ("in_force", "丁。")


def test_promulgation_events_are_linked_to_their_versions(conn: Conn, tmp_path: Path) -> None:
    _anchor(conn, tmp_path)
    _sync(conn, tmp_path, _full_site())

    linked = conn.execute(
        "SELECT ev.event_date, e.version_date FROM corpus.lifecycle_event AS ev"
        "  JOIN corpus.legal_expression AS e ON e.id = ev.expression_id ORDER BY 1"
    ).fetchall()
    assert linked == [(V1, V1), (V2, V2), (V3, V3)]


def test_a_rerun_writes_nothing_and_fetches_no_known_history_page(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    pages = _full_site()
    _sync(conn, tmp_path, pages)
    before = _rows(conn)
    snapshots = conn.execute("SELECT count(*) FROM source.snapshot").fetchone()

    known = {u: p for u, p in pages.items() if "LawContentHistory.aspx" not in u}
    assert _sync(conn, tmp_path, known) == 0

    assert _rows(conn) == before
    assert conn.execute("SELECT count(*) FROM source.snapshot").fetchone() == snapshots


def test_a_missing_version_stops_the_chain_there(conn: Conn, tmp_path: Path) -> None:
    _anchor(conn, tmp_path)
    pages = site("FL000001", TITLE, (V3, V3_TEXT), {12: (V2, V2_TEXT)})  # 制定版沒有全文

    assert _sync(conn, tmp_path, pages) == 0

    assert {d for _, d, _, _ in _rows(conn)} == {V2, V3}
    assert _at(conn, date(2000, 1, 1)) == {}  # 不以 107 年的文字頂替
    # 缺的那一版仍是一版：查不到全文、標題不詳，時間軸接到制定、不留縫。
    expressions = conn.execute(
        "SELECT version_date, text_period, text_status::text, title FROM corpus.legal_expression"
        " ORDER BY version_date"
    ).fetchall()
    assert expressions[0] == (V1, Range(V1, V2, "[)"), "unavailable", None)
    assert [e[2] for e in expressions[1:]] == ["available", "available"]
    problems = conn.execute("SELECT severity, problem_code FROM source.import_problem").fetchall()
    assert ("warning", "history_missing") in problems

    # 來源後來補上了：同一版改為查得到全文，不是另一版。
    assert _sync(conn, tmp_path, _full_site()) == 0
    assert {d for _, d, _, _ in _rows(conn)} == {V1, V2, V3}


def test_a_source_behind_the_corpus_still_backfills_before_the_anchor(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    pages = _full_site(current=(V2, V2_TEXT))  # 主管機關系統還沒更新到 109 年

    assert _sync(conn, tmp_path, pages) == 0

    assert {d for _, d, _, _ in _rows(conn)} == {V1, V2, V3}
    problems = conn.execute("SELECT severity, problem_code FROM source.import_problem").fetchall()
    assert problems == [("warning", "source_behind")]


def test_a_source_ahead_without_the_corpus_version_writes_nothing(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    pages = _full_site(current=(date(2021, 1, 1), V3_TEXT))  # 沒有語料的 109 年版

    assert _sync(conn, tmp_path, pages) == 0

    assert {d for _, d, _, _ in _rows(conn)} == {V3}
    problems = conn.execute("SELECT severity, problem_code FROM source.import_problem").fetchall()
    assert problems == [("error", "current_date_mismatch")]


def test_an_article_unchanged_within_the_source_keeps_the_corpus_text(
    conn: Conn, tmp_path: Path
) -> None:
    """兩個系統的同一條差標點：來源的相鄰兩版相同就是沒修正，沿用語料的文字。"""
    _anchor(conn, tmp_path)
    current = [("1", "甲；"), ("2", "丁。"), ("3", "本法自公布日施行。")]  # 語料是「甲。」
    v2 = [("1", "甲；"), ("2", "丙。"), ("3", "本法自公布日施行。")]
    _sync(conn, tmp_path, _full_site(current=(V3, current), past={12: (V2, v2)}))

    assert _at(conn, date(2019, 1, 1))["art_1"] == ("in_force", "甲。")


def test_unchanged_articles_take_the_newer_version_text(conn: Conn, tmp_path: Path) -> None:
    """與較新版本正規化後相同的條，沿用較新版本的字形：兩個系統的全半形與空白不同，不算修正。"""
    _anchor(conn, tmp_path)
    v2 = [("1", "甲 。"), ("2", "丙。"), ("3", "本法自公布日施行。")]
    _sync(conn, tmp_path, _full_site(past={12: (V2, v2)}))

    assert _at(conn, date(2019, 1, 1))["art_1"] == ("in_force", "甲。")


def test_a_repealed_law_is_checked_against_its_last_version_in_the_history_list(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)
    pages = _full_site(past={13: (V3, V3_TEXT)})
    pages[moenv_law.CURRENT.format(site_id="FL000001")] = current_page(
        TITLE, V1, V3_TEXT, repealed=date(2026, 1, 1)
    )

    assert _sync(conn, tmp_path, pages) == 0

    assert {d for _, d, _, _ in _rows(conn)} == {V1, V2, V3}


def test_an_older_last_article_does_not_date_a_past_version() -> None:
    """末條留著制定時的施行日、沿革這一版沒寫施行：那是較早一次公布的施行日，該條待確認，不猜。"""
    history = (
        "1.中華民國八十年五月二十三日總統令制定公布全文 2 條",
        "2.中華民國一百零七年八月一日總統令修正公布第 1 條條文",
        "3.中華民國一百零九年三月一日總統令修正公布第 1 條條文；並自公布日施行",
    )
    last = "本法自中華民國八十年七月一日施行。"
    anchor = Anchor(
        resource=1,
        pcode=PCODE,
        kind="regulation",
        repealed_on=None,
        version_date=V3,
        latest_date=V3,
        starts={"art_1": V3 + DAY3, "art_2": V3},
        after=[],
        articles={"第 1 條": "丙。", "第 2 條": last},
        latest_articles={"第 1 條": "丙。", "第 2 條": last},
        events=list(history),
    )

    def version(when: date, first: str) -> Version:
        return Version(
            TITLE,
            when,
            [
                Article("第 1 條", first, False, 0),
                Article("第 2 條", last, False, 0),
            ],
        )

    plan = planner.plan(anchor, version(V3, "丙。"), [version(V2, "乙。"), version(V1, "甲。")])

    rows = {
        (v.version_date, u.path): (u.rule, u.starts, u.ends) for v in plan.versions for u in v.units
    }
    assert rows[(V1, "art_1")] == ("specified", date(1991, 7, 1), V3 + DAY3)
    assert rows[(V2, "art_1")] == ("unconfirmed", None, None)
    assert ("warning", "stale_last_article") in {(p.severity, p.code) for p in plan.problems}


def test_a_law_never_amended_is_acquired_and_has_no_past_versions(
    conn: Conn, tmp_path: Path
) -> None:
    _anchor(conn, tmp_path)

    assert _sync(conn, tmp_path, site("FL000001", TITLE, (V3, V3_TEXT), {})) == 0

    assert conn.execute("SELECT count(*) FROM source.item WHERE feed = 'moenv_law'").fetchone() == (
        1,
    )
    assert {d for _, d, _, _ in _rows(conn)} == {V3}


V4 = date(2026, 10, 18)  # 回推之前，每日同步已經入庫的新版本
HISTORY_UNSTATED = (
    HISTORY.rsplit("\r\n", 1)[0] + "\r\n"
    "3.中華民國一百零九年三月一日總統令修正公布第 2 條條文"  # 沒寫施行日：第 2 條待確認
)
HISTORY_V4 = (
    HISTORY_UNSTATED + "\r\n4.中華民國一百十五年十月十八日總統令修正公布第 2 條條文；並自公布日施行"
)
V3_UNSTATED = [("1", "甲。"), ("2", "丁。"), ("3", "戊。")]
V4_TEXT = [("1", "甲。"), ("2", "己。"), ("3", "戊。")]


def _official(history: str, modified: str, text: list[tuple[str, str]]) -> dict[str, Any]:
    articles = [
        {"ArticleType": "A", "ArticleNo": f"第 {n} 條", "ArticleContent": t} for n, t in text
    ]
    return _law(
        PCODE,
        TITLE,
        LawLevel="命令",
        LawHistories=history,
        LawModifiedDate=modified,
        LawArticles=articles,
    )


def test_a_unit_unstated_at_the_anchor_ends_where_a_later_corpus_version_starts_it(
    conn: Conn, tmp_path: Path
) -> None:
    """錨點的起點待確認，錨點之後又入庫了同一條有起點的新版本：更舊的版本止於那個起點。

    只看錨點的話這一條找不到上界，舊版的區間開放，與新版同一條的現行列重疊。
    """
    stored_snapshot(conn, tmp_path, [_official(HISTORY_UNSTATED, "20200301", V3_UNSTATED)])
    importer.run(conn, tmp_path, Moj())
    conn.commit()
    _next(conn, tmp_path, _official(HISTORY_V4, "20261018", V4_TEXT))  # 每日同步的新版本
    pages = _full_site(current=(V4, V4_TEXT), past={13: (V3, V3_UNSTATED)})

    assert _sync(conn, tmp_path, pages) == 0

    art_2 = {d: f for p, d, f, _ in _rows(conn) if p == "art_2"}
    assert art_2[V2] == Range(V2 + DAY3, V4 + DAY3, "[)")  # 錨點待確認，往更新的版本找
    assert art_2[V3] is None  # 錨點本身不動
    assert art_2[V4] == Range(V4 + DAY3, None, "[)")
