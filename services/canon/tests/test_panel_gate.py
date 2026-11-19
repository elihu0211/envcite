"""The panel gate: judges of different families decide by a two-thirds majority."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from canon.db import Conn
from canon.verification.model_gates.panel import PanelGate
from canon.verification.models import ModelError
from tests.model_gates_support import FAITHFUL, UNFAITHFUL, Scripted, _extracted


def _panel(conn: Conn, tmp_path: Path, *votes: tuple[str, Any]) -> Any:
    sid = _extracted(conn, tmp_path)
    judges = tuple(Scripted(name, answer) for name, answer in votes)
    return PanelGate(judges).check(conn, sid)


def test_the_panel_decides_by_a_two_thirds_majority_across_families(
    conn: Conn, tmp_path: Path
) -> None:
    yes = _panel(
        conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-c:1", FAITHFUL), ("fake-d:1", UNFAITHFUL)
    )
    assert yes.verdict == "pass"


def test_the_panel_rejects_on_a_majority_against(conn: Conn, tmp_path: Path) -> None:
    no = _panel(
        conn, tmp_path, ("fake-b:1", UNFAITHFUL), ("fake-c:1", UNFAITHFUL), ("fake-d:1", FAITHFUL)
    )
    assert no.verdict == "fail"
    assert any(v["issues"] for v in no.evidence["votes"])


def test_a_split_panel_and_a_one_family_panel_conclude_nothing(conn: Conn, tmp_path: Path) -> None:
    split = _panel(conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-c:1", UNFAITHFUL))
    assert split.verdict == "inconclusive"

    one_family = _panel(conn, tmp_path, ("fake-b:1", FAITHFUL), ("fake-b:2", FAITHFUL))
    assert one_family.verdict == "inconclusive"
    assert "families" in one_family.evidence["reason"]


def test_judges_of_the_extractors_family_are_left_out_of_the_count(
    conn: Conn, tmp_path: Path
) -> None:
    result = _panel(
        conn, tmp_path, ("fake-a:1", FAITHFUL), ("fake-b:1", FAITHFUL), ("fake-c:1", FAITHFUL)
    )

    assert result.verdict == "pass"
    assert result.evidence["skipped"] == ["fake-a:1"]
    assert len(result.evidence["votes"]) == 2


def test_the_judges_answer_at_the_same_time_and_votes_keep_the_judge_order(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    # 序列呼叫時第一位評審永遠等不到第二位，屏障逾時即失敗
    gather = threading.Barrier(2, timeout=5)

    def reply(prompt: str) -> str:
        gather.wait()
        return json.dumps(FAITHFUL)

    judges = (Scripted("fake-b:1", reply), Scripted("fake-c:1", reply))
    result = PanelGate(judges).check(conn, sid)

    assert result is not None
    assert result.verdict == "pass"
    assert [v["judge"] for v in result.evidence["votes"]] == ["fake-b:1", "fake-c:1"]


def test_a_failing_judge_fails_the_gate_with_the_first_error_in_judge_order(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    late = threading.Event()

    def slow_failure(prompt: str) -> str:
        late.wait(5)
        raise ModelError("fake-b:1 failed")

    def fast_failure(prompt: str) -> str:
        late.set()
        raise ModelError("fake-c:1 failed")

    judges = (Scripted("fake-b:1", slow_failure), Scripted("fake-c:1", fast_failure))

    with pytest.raises(ModelError, match="fake-b:1 failed"):
        PanelGate(judges).check(conn, sid)
