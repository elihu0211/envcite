"""罰則條文沒改、所罰的義務條文修正：罰則依該日的義務條文解析（中央法規標準法第 17 條）。

罰則的連結寫入後不改，指向連結當時的義務陳述；修正後的義務是新的陳述（內容雜湊不同）。
推論與 `ListStatements` 依受罰條文單元加查詢日找該日的義務；修正前的日子仍是原來的連結
（行政罰法第 5 條的跨時點推論）。連結是在單元的數個義務之中挑出的，修正後無從確定對應到
哪一個，不猜。
"""

from datetime import date
from pathlib import Path
from typing import Any

from canon.annotate import load
from canon.annotation_read import list_statements
from canon.db import Conn
from canon.reasoning.engine import Result, evaluate
from canon.reasoning.transition import evaluate_across
from tests.reasoning_support import _plain_duty, _scope, _with
from tests.test_annotation import AFTER, BEFORE, LAW, _doc
from tests.test_incremental import _articles, _first, _law_id, _law_v1, _law_v2, _next

#: 兩個時點都違反（限值 100 與 80），罰則的要件成立、有故意
CASE: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1, "ap_intent": 1}


def _penalty() -> dict[str, Any]:
    """第 2 條：處罰第 1 條的義務；兩個版本的文字相同。"""
    return {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }


def _v1(conn: Conn, tmp_path: Path, *duties: dict[str, Any]) -> dict[str, int]:
    """第 1 條「甲一」是義務、第 2 條「乙一」是罰則，罰則連到 `duty`。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    return dict(
        load(conn, _with(_doc(_plain_duty("甲一", 100), *duties, _penalty()), "serious")).ids
    )


def _v2(conn: Conn, tmp_path: Path, *duties: dict[str, Any]) -> dict[str, int]:
    """2020-03-01 修正第 1 條為「甲二」（限值 80），第 2 條不變；修正後的義務另行標註、確認。"""
    _next(conn, tmp_path, _law_v2(LawArticles=_articles("甲二", "乙一")))
    return dict(load(conn, _doc(_plain_duty("甲二", 80), *duties)).ids)


def _punished(result: Result) -> list[tuple[int, int]]:
    return [(p.statement_id, p.punishes_id) for p in result.penalties]


def test_a_penalty_follows_its_duty_after_the_duty_is_amended(conn: Conn, tmp_path: Path) -> None:
    """修正後的日子：罰則處罰的是修正後的義務，不因舊連結指向舊陳述而少列。"""
    first = _v1(conn, tmp_path)
    second = _v2(conn, tmp_path)

    after = evaluate(conn, AFTER, CASE)

    assert after.verdicts[second["duty"]] == "violation"
    assert _punished(after) == [(first["fine"], second["duty"])]


def test_a_day_before_the_amendment_keeps_the_penalty_on_the_old_duty(
    conn: Conn, tmp_path: Path
) -> None:
    first = _v1(conn, tmp_path)
    _v2(conn, tmp_path)

    before = evaluate(conn, BEFORE, CASE)

    assert _punished(before) == [(first["fine"], first["duty"])]


def test_the_law_at_conduct_and_at_decision_each_punish_their_own_duty(
    conn: Conn, tmp_path: Path
) -> None:
    """行政罰法第 5 條：裁處時與行為時各自推論，罰則各連到當時的義務。"""
    first = _v1(conn, tmp_path)
    second = _v2(conn, tmp_path)

    moved = evaluate_across(conn, BEFORE, AFTER, CASE)

    assert moved.changed
    assert _punished(moved.at_decision) == [(first["fine"], second["duty"])]
    (conduct,) = moved.earlier
    assert _punished(conduct.result) == [(first["fine"], first["duty"])]


def test_a_penalty_linked_to_the_newer_duty_reaches_back_to_the_older_one(
    conn: Conn, tmp_path: Path
) -> None:
    """反方向：罰則在修正後才標註、連到修正後的義務；修正前的日子仍處罰當時的義務。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    old = load(conn, _doc(_plain_duty("甲一", 100))).ids["duty"]
    _next(conn, tmp_path, _law_v2(LawArticles=_articles("甲二", "乙一")))
    new = load(conn, _with(_doc(_plain_duty("甲二", 80), _penalty()), "serious")).ids

    assert _punished(evaluate(conn, AFTER, CASE)) == [(new["fine"], new["duty"])]
    assert _punished(evaluate(conn, BEFORE, CASE)) == [(new["fine"], old)]


def test_list_statements_names_the_duty_the_penalty_punishes_that_day(
    conn: Conn, tmp_path: Path
) -> None:
    """`ListStatements` 與推論用同一個解析：罰則所罰的是當日列出的義務。"""
    first = _v1(conn, tmp_path)
    second = _v2(conn, tmp_path)

    def punishes(on: date) -> int:
        listed = {s.id: s for s in list_statements(conn, _law_id(conn), on).statements}
        return listed[first["fine"]].sanction.punishes

    assert punishes(AFTER) == second["duty"]
    assert punishes(BEFORE) == first["duty"]


def test_a_link_chosen_among_several_duties_of_the_unit_is_not_carried_over(
    conn: Conn, tmp_path: Path
) -> None:
    """修正前的單元有兩個義務、罰則只連其中之一：修正後的義務承接哪一個無從確定，不猜。"""
    _v1(conn, tmp_path, {**_plain_duty("甲一", 50), "id": "other"})
    _v2(conn, tmp_path)

    assert evaluate(conn, AFTER, CASE).penalties == ()


def test_several_duties_after_the_amendment_are_not_guessed_either(
    conn: Conn, tmp_path: Path
) -> None:
    """修正後的單元有兩個義務：罰則處罰哪一個要挑，不由推論代挑。"""
    _v1(conn, tmp_path)
    _v2(conn, tmp_path, {**_plain_duty("甲二", 50), "id": "other"})

    assert evaluate(conn, AFTER, CASE).penalties == ()
