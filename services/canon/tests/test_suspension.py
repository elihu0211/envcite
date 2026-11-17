"""停止適用與恢復適用：沿革匯入後，`corpus.provisions_at` 與推論如何看待停止適用中的條文。

中央法規標準法第 19 條：法規得暫停適用其一部或全部；停止或恢復適用之程序，準用廢止或制定之規定。
停止適用中的條文有文字、版本也仍是現行版，但不是有效規範；恢復適用後回到有效。
"""

from datetime import date
from pathlib import Path

import psycopg
import pytest

from canon.annotate import load
from canon.db import Conn
from tests.test_annotation import _doc, _duty, _limit
from tests.test_import import _scalar
from tests.test_incremental import (
    V1,
    _articles,
    _at,
    _first,
    _law_id,
    _law_v1,
    _next,
    _no_batch_gate,  # noqa: F401  # 一部法規即一整批，比例門檻在此沒有意義
)

#: 2019-01-07 公布：沒寫日期，自公布之日起算至第三日（2019-01-09）停止適用。
SUSPEND = "\r\n3.中華民國一百零八年一月七日總統令公布暫停適用"
#: 2020-01-07 公布，明定自 2020-02-01 起恢復適用。
RESUME = "\r\n4.中華民國一百零九年一月七日總統令公布自一百零九年二月一日起恢復適用"
#: 只停止第 1、2 條。
SUSPEND_ARTICLES = "\r\n3.中華民國一百零八年一月七日總統令公布第1、2條條文暫停適用"
#: 停止適用之後修正第 2 條：新的第 2 條是新公布的文字，不在原停止適用的範圍內。
AMEND_ART_2 = "\r\n4.中華民國一百零九年三月一日總統令修正公布第 2 條條文；並自公布日施行"


def _statuses(conn: Conn, on: date) -> dict[str, str]:
    return {path: status for path, (status, _) in _at(conn, on).items()}


def test_a_suspended_law_is_not_in_force_until_it_resumes(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawHistories=V1 + SUSPEND + RESUME))

    assert _statuses(conn, date(2019, 1, 8)) == {"art_1": "in_force", "art_2": "in_force"}
    assert _statuses(conn, date(2019, 1, 9)) == {"art_1": "suspended", "art_2": "suspended"}
    assert _statuses(conn, date(2020, 1, 31)) == {"art_1": "suspended", "art_2": "suspended"}
    assert _statuses(conn, date(2020, 2, 1)) == {"art_1": "in_force", "art_2": "in_force"}


def test_a_suspended_article_keeps_its_text_and_version(conn: Conn, tmp_path: Path) -> None:
    """停止適用不是廢止：文字與現行版本都在，只是不適用。"""
    _first(conn, tmp_path, _law_v1(LawHistories=V1 + SUSPEND))

    assert _at(conn, date(2024, 1, 1)) == {
        "art_1": ("suspended", "甲一"),
        "art_2": ("suspended", "乙一"),
    }


def test_only_the_listed_articles_are_suspended(conn: Conn, tmp_path: Path) -> None:
    law = _law_v1(LawHistories=V1 + SUSPEND_ARTICLES, LawArticles=_articles("甲一", "乙一", "丙一"))
    _first(conn, tmp_path, law)

    assert _statuses(conn, date(2019, 6, 1)) == {
        "art_1": "suspended",
        "art_2": "suspended",
        "art_3": "in_force",
    }


def test_text_promulgated_after_the_suspension_is_not_suspended(conn: Conn, tmp_path: Path) -> None:
    """停止適用及於當時的條文；之後公布而觸及該條的新文字，依它自己的施行日生效。"""
    articles = _articles("甲一", "乙一", "丙一")
    _first(conn, tmp_path, _law_v1(LawHistories=V1 + SUSPEND_ARTICLES, LawArticles=articles))
    amended = _law_v1(
        LawHistories=V1 + SUSPEND_ARTICLES + AMEND_ART_2,
        LawModifiedDate="20200301",
        LawArticles=_articles("甲一", "乙二", "丙一"),
    )
    assert _next(conn, tmp_path, amended).actions == {"versioned": 1}

    # 修正前：第 2 條舊文仍在停止適用中。
    assert _at(conn, date(2020, 3, 2))["art_2"] == ("suspended", "乙一")
    assert _at(conn, date(2020, 6, 1)) == {
        "art_1": ("suspended", "甲一"),
        "art_2": ("in_force", "乙二"),
        "art_3": ("in_force", "丙一"),
    }


def test_reasoning_drops_statements_on_suspended_text_and_takes_them_back_on_resumption(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawHistories=V1 + SUSPEND + RESUME))
    load(conn, _doc(_duty(), _limit("乙一", 100)))

    def facts(on: date) -> int:
        return int(
            _scalar(
                conn,
                "SELECT count(*) FROM annotation.statements_at(%s, 'zho', %s)",
                _law_id(conn),
                on,
            )
        )

    assert facts(date(2019, 1, 8)) == 2
    assert facts(date(2019, 6, 1)) == 0
    assert facts(date(2020, 2, 1)) == 2


def test_the_suspension_and_its_resumption_are_events_with_their_effective_dates(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawHistories=V1 + SUSPEND_ARTICLES + RESUME))

    assert conn.execute(
        "SELECT event_kind::text, event_date, effective_on, articles"
        "  FROM corpus.lifecycle_event"
        " WHERE event_kind IN ('suspension', 'resumption') ORDER BY event_date"
    ).fetchall() == [
        ("suspension", date(2019, 1, 7), date(2019, 1, 9), ["art_1", "art_2"]),
        ("resumption", date(2020, 1, 7), date(2020, 2, 1), None),
    ]


@pytest.mark.parametrize(
    ("kind", "effective_on", "articles"),
    [
        ("enactment", date(2020, 1, 3), None),
        ("repeal", None, ["art_1"]),
        ("resumption", None, None),
        ("suspension", date(2020, 1, 3), ["第 1 條"]),
    ],
)
def test_effective_dates_and_article_lists_belong_to_the_kinds_that_have_them(
    conn: Conn, tmp_path: Path, kind: str, effective_on: date | None, articles: list[str] | None
) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO corpus.lifecycle_event"
            " (resource_id, event_kind, event_date, source_text, effective_on, articles)"
            " VALUES (%s, %s::corpus.event_kind, '2020-01-01', '原文', %s, %s)",
            (_law_id(conn), kind, effective_on, articles),
        )
