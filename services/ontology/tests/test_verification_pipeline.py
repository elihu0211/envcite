"""The verification pipeline end to end: one row per gate, crash safety, tampering (envcite-
docs 02-06 §3.3)."""

import pytest

from envcite_ontology.reasoning import evaluate
from envcite_ontology.verification import (
    DEFAULT_GATES,
    settle,
    verify,
)
from envcite_ontology.verification.gates import GateResult
from tests.reasoning_support import WATER_DAY
from tests.test_import import _scalar
from tests.verification_support import (
    DETERMINISTIC,
    Conn,
    _latest,
    _review,
    _water,
    _water_with,
)


def test_verify_writes_one_row_per_gate_and_reruns_only_the_inconclusive(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids

    first = verify(conn)
    second = verify(conn)

    assert first == 13 * len(DEFAULT_GATES)  # 每個陳述、每個閘門一列（不適用也記）
    assert second <= first
    assert _latest(conn, ids["w7_2a"], "executable") == "not_applicable"


class _DiesOnTheThird:
    """A gate whose process is killed while checking the third statement."""

    method = "dies"
    verifier = "gate:dies@1"

    def __init__(self) -> None:
        self.calls = 0

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("the process died")
        return GateResult("pass", {})


def test_a_crash_keeps_the_verdicts_already_recorded(conn: Conn, sample: None) -> None:
    """模型呼叫是最貴的：中途崩潰不能丟掉已經問到的答案，長時間的跑在資料庫裡也要看得見進度。"""
    _water(conn, sample)

    with pytest.raises(RuntimeError, match="died"):
        verify(conn, [_DiesOnTheThird()])
    conn.rollback()  # 崩潰的程序沒有機會 commit 任何東西

    assert _scalar(conn, "SELECT count(*) FROM annotation.verification WHERE method = 'dies'") == 2


def test_the_water_draft_is_accepted_without_a_human_where_the_gates_can_decide(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids

    result = settle(conn, DETERMINISTIC)

    # 義務、許可與十個罰則通過。授權是構成性，三個閘門都不適用，維持 extracted。
    assert (result.confirmed, result.rejected) == (12, 0)
    assert _review(conn, ids["w7_1"]) == "confirmed"
    assert _review(conn, ids["p40_1_fine"]) == "confirmed"
    assert _review(conn, ids["w7_2b"]) == "confirmed"
    assert _review(conn, ids["w7_2a"]) == "extracted"

    case = {
        "actor_is_regulated_entity": 1,
        "discharges_to_surface_water": 1,
        "actor_is_business_or_sewer_system": 1,
        "actor_is_livestock_business": 0,
        "improvement_overdue": 0,
        "effluent_meets_standard": 0,
        "circumstances_serious": 1,
        "revocation_necessary": 0,
    }
    verdicts = evaluate(conn, WATER_DAY, case).verdicts
    assert verdicts == {ids["w7_1"]: "violation"}  # 接受之後推論才看得到它


def test_a_tampered_statement_is_rejected_by_the_pipeline(conn: Conn, sample: None) -> None:
    ids = _water_with(conn, "p40_1_fine", min=600000).ids

    result = settle(conn, DETERMINISTIC)

    assert result.rejected == 1
    assert _review(conn, ids["p40_1_fine"]) == "rejected"
    assert evaluate(conn, WATER_DAY, {"effluent_meets_standard": 0}).penalties == ()
