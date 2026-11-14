"""結構守門：模組不得長成巨石，邏輯不直接讀系統時鐘。

單一模組超過 `MAX_LINES` 行，就是在一個檔案裡放了不只一個職責：把職責拆到各自的模組，
不要調高上限。
"""

import ast
from pathlib import Path

import envcite_ontology

SRC = Path(envcite_ontology.__file__).parent
MAX_LINES = 500


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_outgrows_the_limit() -> None:
    too_big = {
        path.relative_to(SRC).as_posix(): _lines(path)
        for path in _modules()
        if _lines(path) > MAX_LINES
    }

    assert not too_big, (
        f"超過 {MAX_LINES} 行的模組（行數）：{too_big}。把職責拆到各自的模組；不要調高上限。"
    )


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
