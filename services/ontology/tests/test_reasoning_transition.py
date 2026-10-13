"""行政罰法第 5 條：法律在行為後有變更，裁處時之法律與裁處前之法律各自推論，不替呼叫端選。"""

from datetime import date
from pathlib import Path

import pytest

from env_law_ontology.annotate import load
from env_law_ontology.reasoning import evaluate_across
from tests.reasoning_support import Conn
from tests.test_annotation import _doc, _duty, _limit
from tests.test_incremental import _first, _law_v1, _law_v2, _next

CASE: dict[str, float] = {"discharging": 1, "cod": 90}
CONDUCT = date(2019, 1, 1)  # 限值 100
DECIDED = date(2020, 6, 1)  # 第 2 條已修正，限值 80


def _amended(conn: Conn, tmp_path: Path) -> tuple[int, int]:
    """v1 的限值 100，2020-03-01 公布修正為 80。回傳（義務, 新限值）的陳述編號。"""
    _first(conn, tmp_path, _law_v1())
    first = load(conn, _doc(_duty(), _limit("乙一", 100)))
    _next(conn, tmp_path, _law_v2())
    second = load(conn, _doc(_limit("乙二", 80)))
    return first.ids["duty"], second.ids["limit80"]


def test_a_law_changed_after_the_conduct_is_reported_for_both_times(
    conn: Conn, tmp_path: Path
) -> None:
    duty, _ = _amended(conn, tmp_path)

    moved = evaluate_across(conn, CONDUCT, DECIDED, CASE)

    assert moved.changed
    assert moved.at_decision.verdicts[duty] == "violation"  # 裁處時：90 > 80
    (before,) = moved.earlier
    assert before.as_of == CONDUCT
    assert before.result.verdicts[duty] == "compliant"  # 行為時：90 <= 100


def test_no_change_between_conduct_and_decision_is_one_law(conn: Conn, tmp_path: Path) -> None:
    _amended(conn, tmp_path)

    still = evaluate_across(conn, CONDUCT, date(2019, 6, 1), CASE)

    assert not still.changed
    assert still.earlier == ()


def test_conduct_after_decision_is_refused(conn: Conn, tmp_path: Path) -> None:
    _amended(conn, tmp_path)

    with pytest.raises(ValueError, match="conduct_on"):
        evaluate_across(conn, DECIDED, CONDUCT, CASE)


def test_a_change_that_is_back_to_the_same_statements_is_not_a_second_law(
    conn: Conn, tmp_path: Path
) -> None:
    """裁處時與行為時是同一組陳述（中間改過又改回）：沒有可比的另一部法律。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))

    same = evaluate_across(conn, CONDUCT, CONDUCT, CASE)

    assert not same.changed
