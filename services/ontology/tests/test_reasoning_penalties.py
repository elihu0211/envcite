"""The reasoning engine and penalties: the general part blocks punishment, limitation, one act
or several."""

from datetime import date
from pathlib import Path
from typing import Any

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import Result, evaluate
from tests.reasoning_support import (
    Conn,
    _plain_duty,
    _scope,
    _with,
)
from tests.test_annotation import BEFORE, LAW, _doc
from tests.test_incremental import _articles, _first, _law_v1


def test_the_general_part_blocks_punishment_and_leaves_the_duty(conn: Conn, tmp_path: Path) -> None:
    """行政罰法總則排除處罰，不改義務的涵攝。有責性未知則附條件，不當成可罰。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    penalty = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    loaded = load(conn, _with(_doc(_plain_duty(), penalty), "serious"))
    duty, fine = loaded.ids["duty"], loaded.ids["fine"]
    over: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1}

    def run(extra: dict[str, float]) -> Result:
        return evaluate(conn, BEFORE, over | extra)

    unknown = run({})
    assert unknown.verdicts[duty] == "violation"
    assert unknown.penalties[0].conditional
    assert unknown.general.unpunished == ()

    assert run({"ap_intent": 1}).penalties[0].conditional is False

    none = run({"ap_intent": 0, "ap_negligence": 0})
    assert none.verdicts[duty] == "violation"
    assert none.penalties == ()
    assert none.general.unpunished == ((fine, "no_fault"),)

    presumed = run({"ap_intent": 0, "ap_negligence": 0, "ap_agent_fault": 1})
    assert presumed.penalties[0].conditional is False
    assert presumed.general.presumed == ((fine, "agent_fault"),)

    child = run({"ap_intent": 1, "ap_age": 13})
    assert child.penalties == ()
    assert (fine, "under_fourteen") in child.general.unpunished

    youth = run({"ap_intent": 1, "ap_age": 16})
    assert youth.penalties[0].conditional is False
    assert (fine, "youth") in youth.general.mitigation

    assert (fine, "incapable") in run(
        {"ap_intent": 1, "ap_incapable": 1, "ap_self_induced": 0}
    ).general.unpunished
    induced = run({"ap_intent": 1, "ap_incapable": 1, "ap_self_induced": 1})
    assert induced.penalties != () and induced.general.unpunished == ()
    assert run({"ap_intent": 1, "ap_incapable": 1}).general.unpunished == ()

    assert run({"ap_intent": 1, "ap_superior_order": 1}).penalties[0].conditional
    refused = run(
        {
            "ap_intent": 1,
            "ap_superior_order": 1,
            "ap_knew_illegal": 1,
            "ap_stated_opinion": 0,
        }
    )
    assert refused.penalties[0].conditional is False and refused.general.unpunished == ()

    ignorant = run({"ap_intent": 1, "ap_ignorance_of_law": 1})
    assert ignorant.penalties[0].conditional is False
    assert (fine, "ignorance") in ignorant.general.mitigation


def test_limitation_marks_a_listed_penalty_and_leaves_it_there(conn: Conn, tmp_path: Path) -> None:
    """三年自行為終了起算；結果較晚則改自結果日。起算日不明不標已逾。罰則仍列出。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    penalty = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "sanction",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    loaded = load(conn, _with(_doc(_plain_duty(), penalty), "serious"))
    duty, fine = loaded.ids["duty"], loaded.ids["fine"]
    base: dict[str, float] = {"discharging": 1, "cod": 150, "serious": 1, "ap_intent": 1}

    def at(day: date, **extra: float) -> Result:
        return evaluate(conn, day, base | extra)

    open_period = at(date(2021, 1, 15), ap_conduct_ended=20180115)
    assert open_period.verdicts[duty] == "violation"
    assert open_period.penalties[0].statement_id == fine
    assert open_period.general.time_barred == ()

    closed = at(date(2021, 1, 16), ap_conduct_ended=20180115)
    assert closed.penalties[0].statement_id == fine
    assert closed.general.time_barred == (fine,)

    # 結果日較晚：2018-06-01 終了、2020-01-15 結果發生，末日是 2023-01-15。
    later = at(date(2023, 1, 15), ap_conduct_ended=20180601, ap_result_on=20200115)
    assert later.general.time_barred == ()
    overdue = at(date(2023, 1, 16), ap_conduct_ended=20180601, ap_result_on=20200115)
    assert overdue.general.time_barred == (fine,)

    assert at(date(2030, 1, 1), ap_result_on=20180115).general.time_barred == ()
    assert at(date(2030, 1, 1)).general.time_barred == ()

    # 2020-02-28 終了，始日 02-29，三年後無相當日，末日是 2023-02-28。
    leap = at(date(2023, 2, 28), ap_conduct_ended=20200228)
    assert leap.general.time_barred == ()
    assert at(date(2023, 3, 1), ap_conduct_ended=20200228).general.time_barred == (fine,)

    bad = at(date(2030, 1, 1), ap_conduct_ended=20180231)
    assert bad.general.time_barred == ()
    assert ("bad_date", "ap_conduct_ended") in bad.problems


def test_one_act_and_several_acts_are_both_reported(conn: Conn, tmp_path: Path) -> None:
    """第 24 條是合併後的一個區間，第 25 條是各則自己的額度。不選。附條件、按次、缺額度者不併入。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))

    def penalty(name: str, scope: str, sanction: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": name,
            "law": LAW,
            "path": "art_2",
            "text": "乙一",
            "kind": "sanction",
            "conditions": [_scope(scope)],
            "sanction": {"punishes": "duty", **sanction},
        }

    loaded = load(
        conn,
        _with(
            _doc(
                _plain_duty(),
                penalty("wide", "wide", {"effect": "fine", "min": 1000, "max": 10000}),
                penalty("high", "high", {"effect": "fine", "min": 3000, "max": 6000}),
                penalty("seized", "wide", {"effect": "confiscation"}),
                penalty("again", "overdue", {"effect": "fine", "repeating": True}),
                penalty("open", "open_bound", {"effect": "fine", "max": 50000}),
            ),
            "wide",
            "high",
            "overdue",
            "open_bound",
        ),
    )
    wide, high = loaded.ids["wide"], loaded.ids["high"]
    base: dict[str, float] = {"discharging": 1, "cod": 150, "wide": 1, "high": 1, "ap_intent": 1}

    def run(**extra: float) -> Result:
        return evaluate(conn, BEFORE, base | extra)

    both = run()
    assert both.verdicts[loaded.ids["duty"]] == "violation"
    assert both.one_act == (3000, 10000)
    own = {p.statement_id: (p.min_amount, p.max_amount, p.effect) for p in both.penalties}
    assert own[wide] == (1000, 10000, "fine")
    assert own[high] == (3000, 6000, "fine")
    assert own[loaded.ids["seized"]][2] == "confiscation"

    only_wide = evaluate(conn, BEFORE, base | {"high": 0})
    assert only_wide.one_act == (1000, 10000)
    assert high not in {p.statement_id for p in only_wide.penalties}

    unknown = evaluate(conn, BEFORE, {k: v for k, v in base.items() if k != "ap_intent"})
    assert unknown.penalties and unknown.one_act is None

    repeated = run(overdue=1)
    assert repeated.one_act == (3000, 10000)
    assert any(p.statement_id == loaded.ids["again"] and p.repeating for p in repeated.penalties)

    assert run(open_bound=1).one_act is None

    overdue = evaluate(conn, date(2020, 1, 1), base | {"ap_conduct_ended": 20150101})
    assert overdue.one_act == (3000, 10000)
    assert wide in overdue.general.time_barred and high in overdue.general.time_barred

    clear = evaluate(conn, BEFORE, base | {"cod": 50})
    assert clear.penalties == () and clear.one_act is None


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


def test_an_improvement_order_is_not_a_punishment_and_the_general_part_does_not_reach_it(
    conn: Conn, tmp_path: Path
) -> None:
    """改善命令是預防性處分：無故意過失、裁處權時效已過，罰鍰不列，命令照列。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    loaded = load(
        conn,
        _with(
            _doc(
                _plain_duty(),
                _penalty("fine", "fine", min=1, max=2),
                _penalty("order", "order", discretionary=True),
            ),
            "serious",
        ),
    )
    fine, order = loaded.ids["fine"], loaded.ids["order"]
    case: dict[str, float] = {
        "discharging": 1,
        "cod": 150,
        "serious": 1,
        "ap_intent": 0,
        "ap_negligence": 0,
        "ap_conduct_ended": 20150115,
    }

    result = evaluate(conn, date(2021, 1, 16), case)

    assert [p.statement_id for p in result.penalties] == [order]
    assert result.penalties[0].discretionary
    assert result.general.unpunished == ((fine, "no_fault"),)
    assert result.general.time_barred == ()

    barred = evaluate(conn, date(2021, 1, 16), case | {"ap_intent": 1})
    assert {p.statement_id for p in barred.penalties} == {fine, order}
    assert barred.general.time_barred == (fine,)  # 命令不受裁處權時效約束
