"""行政罰法第 18 條（減輕的額度、所得利益的加重）與第 26 條（刑事優先、再裁處、扣抵）。

條文依全國法規資料庫的行政罰法（民國 111 年 6 月 15 日修正）。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import Result, evaluate
from tests.reasoning_support import Conn, _plain_duty, _scope, _with
from tests.test_annotation import BEFORE, LAW, _doc
from tests.test_incremental import _articles, _first, _law_v1


def _penalty(name: str, effect: str, **sanction: Any) -> dict[str, Any]:
    return {
        "id": name,
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": effect, **sanction},
    }


def _loaded(conn: Conn, tmp_path: Path, *penalties: dict[str, Any]) -> dict[str, int]:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    return dict(load(conn, _with(_doc(_plain_duty(), *penalties), "serious")).ids)


BASE: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1, "ap_intent": 1}


def _run(conn: Conn, on: date = BEFORE, /, **extra: float) -> Result:
    return evaluate(conn, on, BASE | extra)


def test_a_mitigated_fine_gets_half_or_a_third_of_its_range(conn: Conn, tmp_path: Path) -> None:
    """第 18 條第 3 項：二分之一；事由同時得免除（第 8、12、13 條）三分之一。下限進位、上限捨去。"""
    ids = _loaded(conn, tmp_path, _penalty("fine", "fine", min=10001, max=100000))
    fine = ids["fine"]

    assert _run(conn).general.reduced == ()
    assert _run(conn, ap_age=16).general.reduced == ((fine, "youth", 2, 5001, 50000),)
    assert _run(conn, ap_ignorance_of_law=1).general.reduced == (
        (fine, "ignorance", 3, 3334, 33333),
    )
    both = _run(conn, ap_age=16, ap_ignorance_of_law=1).general.reduced
    assert {(g, k) for _, g, k, _, _ in both} == {("youth", 2), ("ignorance", 3)}


def test_reduction_keeps_a_missing_bound_missing_and_skips_other_effects(
    conn: Conn, tmp_path: Path
) -> None:
    ids = _loaded(
        conn,
        tmp_path,
        _penalty("capped", "fine", max=30000),
        _penalty("open", "fine"),
        _penalty("closure", "restriction"),
    )

    reduced = _run(conn, ap_age=16).general.reduced

    assert reduced == ((ids["capped"], "youth", 2, None, 15000),)


def test_a_benefit_above_the_cap_allows_raising_it(conn: Conn, tmp_path: Path) -> None:
    """第 18 條第 2 項：利益超過最高額才得加重，等於最高額不算。"""
    ids = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=60000))

    assert _run(conn, ap_gained_benefit=60000).general.raised_caps == ()
    assert _run(conn, ap_gained_benefit=3_000_000_000).general.raised_caps == (
        (ids["fine"], Decimal("3000000000")),
    )


def test_a_criminal_offense_withholds_the_fine_but_not_other_penalties(
    conn: Conn, tmp_path: Path
) -> None:
    """第 26 條第 1 項：罰鍰不列；其他種類行政罰照列；沒入看法院是否已宣告沒收，未知附條件。"""
    ids = _loaded(
        conn,
        tmp_path,
        _penalty("fine", "fine", min=1, max=2),
        _penalty("closure", "restriction"),
        _penalty("seize", "confiscation"),
    )
    fine, closure, seize = ids["fine"], ids["closure"], ids["seize"]

    first = _run(conn, ap_criminal_offense=1)
    assert {p.statement_id: p.conditional for p in first.penalties} == {
        closure: False,
        seize: True,
    }
    assert (fine, "criminal_priority") in first.general.unpunished
    assert first.one_act is None

    seized_by_court = _run(conn, ap_criminal_offense=1, ap_court_confiscated=1)
    assert {p.statement_id for p in seized_by_court.penalties} == {closure}
    assert (seize, "criminal_priority") in seized_by_court.general.unpunished

    not_seized = _run(conn, ap_criminal_offense=1, ap_court_confiscated=0)
    assert {p.statement_id: p.conditional for p in not_seized.penalties} == {
        closure: False,
        seize: False,
    }

    # 未提供是否觸犯刑事法律：不套用；明白說沒有：照常
    no_case: list[dict[str, float]] = [{}, {"ap_criminal_offense": 0}]
    for case in no_case:
        plain = _run(conn, **case)
        assert {p.statement_id for p in plain.penalties} == {fine, closure, seize}
        assert plain.general.unpunished == ()


def test_a_criminal_case_closed_without_conviction_lets_the_fine_through(
    conn: Conn, tmp_path: Path
) -> None:
    """第 26 條第 2 至 4 項：不起訴等確定即得裁處；支付的金額與勞務（時數乘基本工資）扣抵。"""
    ids = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=60000))
    fine = ids["fine"]

    cleared = _run(conn, ap_criminal_offense=1, ap_criminal_cleared=20181201)
    assert [p.statement_id for p in cleared.penalties] == [fine]
    assert cleared.general.unpunished == ()
    assert cleared.general.deductions == ()

    paid = _run(
        conn,
        ap_criminal_cleared=20181201,
        ap_criminal_payment=5000,
        ap_community_service_hours=10.5,
        ap_minimum_hourly_wage=150,
    )
    assert paid.general.deductions == ((fine, Decimal("6575.0")),)

    no_wage = _run(conn, ap_criminal_cleared=20181201, ap_community_service_hours=10)
    assert no_wage.general.deductions == ()
    assert (fine, "ap_minimum_hourly_wage") in no_wage.missing

    # 刑事程序尚未確定時不扣抵：罰鍰本來就不列
    pending = _run(conn, ap_criminal_offense=1, ap_criminal_payment=5000)
    assert pending.penalties == () and pending.general.deductions == ()


def test_limitation_runs_from_the_criminal_clearance(conn: Conn, tmp_path: Path) -> None:
    """第 27 條第 3 項：第 26 條第 2 項的情形，三年自不起訴等確定之日起算。"""
    ids = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1, max=2))
    fine = ids["fine"]
    facts: dict[str, float] = {"ap_conduct_ended": 20150115, "ap_criminal_cleared": 20190110}

    assert _run(conn, date(2022, 1, 10), **facts).general.time_barred == ()
    assert _run(conn, date(2022, 1, 11), **facts).general.time_barred == (fine,)
    assert _run(conn, date(2019, 2, 1), ap_conduct_ended=20150115).general.time_barred == (fine,)
