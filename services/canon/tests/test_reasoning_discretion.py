"""行政罰法第 8 條但書與第 19 條：法律效果層次的裁量，不是阻卻責任。

「得」的裁量由裁罰機關依個案決定，引擎只列候選。第 8 條不知法規照列罰則，免除是候選；
第 19 條只免罰鍰，法定最高額由引擎核對。
"""

from pathlib import Path

from canon.db import Conn
from canon.reasoning.engine import Discretion, Result
from tests.test_reasoning_fines import _loaded, _penalty, _run


def _ids(result: Result) -> set[int]:
    return {p.statement_id for p in result.penalties}


def test_unavoidable_ignorance_is_a_note_not_an_excuse(conn: Conn, tmp_path: Path) -> None:
    """無法避免不再自動免除：罰則照列，免除是候選，「無法避免」只是情節附註。"""
    fine = _loaded(conn, tmp_path, _penalty("fine", "fine", min=10001, max=100000))["fine"]

    res = _run(conn, ap_ignorance_unavoidable=1)

    assert _ids(res) == {fine}
    assert res.general.unpunished == () and res.general.excused == ()
    assert res.general.mitigation == ((fine, "ignorance"),)
    assert res.general.reduced == ((fine, "ignorance", 3, 3334, 33333),)
    assert res.general.discretion == (
        Discretion(fine, "ignorance_of_law", ("exempt", "reduce"), ("unavoidable",)),
    )


def test_avoidable_ignorance_is_also_a_candidate_without_the_note(
    conn: Conn, tmp_path: Path
) -> None:
    fine = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=30000))["fine"]

    res = _run(conn, ap_ignorance_of_law=1)

    assert _ids(res) == {fine}
    assert res.general.discretion == (Discretion(fine, "ignorance_of_law", ("exempt", "reduce")),)


def test_a_caller_who_says_the_actor_knew_the_duty_gets_no_section_8(
    conn: Conn, tmp_path: Path
) -> None:
    fine = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=30000))["fine"]

    assert _run(conn).general.discretion == ()
    for extra in (
        {"ap_ignorance_of_law": 0},
        {"ap_ignorance_of_law": 0, "ap_ignorance_unavoidable": 1},
    ):
        res = _run(conn, **extra)
        assert _ids(res) == {fine}
        assert res.general.discretion == ()
        assert res.general.mitigation == () and res.general.reduced == ()


def test_only_a_quantifiable_penalty_can_be_reduced(conn: Conn, tmp_path: Path) -> None:
    """立法理由：減輕只適用於可量化的處罰；免除不限種類。"""
    ids = _loaded(
        conn,
        tmp_path,
        _penalty("fine", "fine", min=1000, max=30000),
        _penalty("closure", "restriction"),
    )

    res = _run(conn, ap_ignorance_of_law=1)

    assert res.general.discretion == (
        Discretion(ids["fine"], "ignorance_of_law", ("exempt", "reduce")),
        Discretion(ids["closure"], "ignorance_of_law", ("exempt",)),
    )


def test_a_petty_fine_is_waived_and_other_penalties_stay(conn: Conn, tmp_path: Path) -> None:
    """第 19 條：只免罰鍰，沒入等其他處罰照列；附第 2 項糾正或勸導的候選措施。"""
    ids = _loaded(
        conn,
        tmp_path,
        _penalty("fine", "fine", min=1000, max=3000),
        _penalty("seizure", "confiscation"),
    )

    res = _run(conn, ap_petty_minor=1)

    assert _ids(res) == {ids["seizure"]}
    assert res.general.unpunished == ()
    assert res.general.discretion == (
        Discretion(ids["fine"], "petty_offense", ("correct_or_advise",)),
    )
    assert _ids(_run(conn)) == {ids["fine"], ids["seizure"]}


def test_a_fine_whose_cap_is_not_three_thousand_or_less_is_not_waived(
    conn: Conn, tmp_path: Path
) -> None:
    """最高額由引擎核對，不信呼叫端；最高額超過 3,000 元或沒有數額都不適用。"""
    ids = _loaded(
        conn,
        tmp_path,
        _penalty("over", "fine", min=1000, max=3001),
        _penalty("open", "fine", min=1000),
    )

    res = _run(conn, ap_petty_minor=1)

    assert _ids(res) == {ids["over"], ids["open"]}
    assert res.general.discretion == ()


def test_section_19_needs_the_penalty_to_stand_and_yields_to_other_bars(
    conn: Conn, tmp_path: Path
) -> None:
    """處罰要件已成立為前提：另有不罰事由時，只列該事由，不列第 19 條。"""
    fine = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=3000))["fine"]

    no_fault = _run(conn, ap_petty_minor=1, ap_intent=0, ap_negligence=0)
    assert no_fault.general.unpunished == ((fine, "no_fault"),)
    assert no_fault.general.discretion == ()

    criminal = _run(conn, ap_petty_minor=1, ap_criminal_offense=1)
    assert criminal.general.unpunished == ((fine, "criminal_priority"),)
    assert criminal.general.discretion == ()


def test_a_waived_fine_takes_no_section_8_reduction(conn: Conn, tmp_path: Path) -> None:
    fine = _loaded(conn, tmp_path, _penalty("fine", "fine", min=1000, max=3000))["fine"]

    res = _run(conn, ap_petty_minor=1, ap_ignorance_of_law=1)

    assert res.penalties == ()
    assert res.general.mitigation == () and res.general.reduced == ()
    assert res.general.discretion == (Discretion(fine, "petty_offense", ("correct_or_advise",)),)
