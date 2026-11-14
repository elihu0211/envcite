"""The panel gate: judges of different families decide by a two-thirds majority."""

from pathlib import Path
from typing import Any

from envcite_ontology.verification.model_gates.panel import PanelGate
from tests.model_gates_support import (
    FAITHFUL,
    UNFAITHFUL,
    Conn,
    Scripted,
    _extracted,
)


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
