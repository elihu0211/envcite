"""結構守門：模組不得長成巨石。

單一模組超過 `MAX_LINES` 行，就是在一個檔案裡放了不只一個職責。已經過大的模組列在 `OVERSIZED`，
上限是目前的行數（取整到 50）：只能縮、不能增。拆完就從清單刪掉；新模組一律以 `MAX_LINES` 為限。
"""

import ast
from pathlib import Path

import pytest

import envcite_ontology

SRC = Path(envcite_ontology.__file__).parent
MAX_LINES = 500

#: 已知過大的模組 → 上限（行）。拆分時刪掉對應的項目，不要調高。
OVERSIZED = {
    "verification/model_gates.py": 550,
    "verification/run.py": 550,
}


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_outgrows_its_limit() -> None:
    too_big = {
        rel: (_lines(path), OVERSIZED.get(rel, MAX_LINES))
        for path in _modules()
        if _lines(path) > OVERSIZED.get(rel := path.relative_to(SRC).as_posix(), MAX_LINES)
    }

    assert not too_big, (
        f"超過行數上限的模組（行數, 上限）：{too_big}。"
        f"把職責拆到各自的模組；不要調高 `OVERSIZED` 的上限。"
    )


@pytest.mark.parametrize("rel", sorted(OVERSIZED))
def test_a_module_that_was_split_leaves_the_oversized_list(rel: str) -> None:
    path = SRC / rel

    assert path.exists(), f"{rel} 已不存在：從 `OVERSIZED` 刪掉"
    assert _lines(path) > MAX_LINES, f"{rel} 已在 {MAX_LINES} 行以內：從 `OVERSIZED` 刪掉"


def _clock_reads(path: Path) -> list[int]:
    """程式裡直接呼叫系統時鐘（`date.today()`、`datetime.now()` 等）的行號。"""
    reads = {"today", "now", "utcnow"}
    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in reads
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"date", "datetime"}
    ]


def test_logic_takes_today_instead_of_reading_the_clock() -> None:
    reads = {
        rel: lines
        for path in _modules()
        if (lines := _clock_reads(path)) and (rel := path.relative_to(SRC).as_posix())
    }

    assert not reads, f"直接讀系統時鐘：{reads}。改收注入的 `clock.Today`（預設 `date.today`）。"
