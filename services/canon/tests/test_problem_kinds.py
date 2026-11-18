"""推論會產生的問題種類、線路上的對映與契約列舉三者同步。

規則檔與案件輸入各自寫 `problem(<種類>, …)`；種類沒有對映，`Outcome` 組裝時查表會丟 `KeyError`，
整個 `Evaluate` 變成 `UNKNOWN`。這裡不啟動推論，只比對三份清單。
"""

import re
from pathlib import Path

import canon.reasoning
from canon.reasoning_codec.enums import PROBLEM_KIND
from envcite.canon.v1 import reasoning_pb2 as pb

_KIND = re.compile(r"problem\(([a-z_]+),")


def _produced() -> set[str]:
    """規則檔與推論套件的 Python 原始碼裡，寫成 `problem(<種類>, …)` 的種類（註解不算）。"""
    root = Path(canon.reasoning.__file__).parent
    kinds: set[str] = set()
    for path in [*root.rglob("*.lp"), *root.rglob("*.py")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("%", 1)[0] if path.suffix == ".lp" else line
            kinds.update(_KIND.findall(code))
    return kinds


def test_every_problem_the_reasoning_can_raise_has_a_wire_kind() -> None:
    assert _produced() - set(PROBLEM_KIND) == set()


def test_no_wire_kind_is_unreachable() -> None:
    """對映表裡沒有推論永遠不會產生的死項。"""
    assert set(PROBLEM_KIND) - _produced() == set()


def test_every_contract_kind_is_mapped_exactly_once() -> None:
    declared = {v for k, v in pb.ProblemKind.items() if v != pb.PROBLEM_KIND_UNSPECIFIED}
    assert sorted(PROBLEM_KIND.values()) == sorted(declared)
