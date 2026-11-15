"""結構守門：模組不得長成巨石，邏輯不直接讀系統時鐘，核心不匯入本體子包。

單一模組超過 `MAX_LINES` 行，就是在一個檔案裡放了不只一個職責：把職責拆到各自的模組，
不要調高上限。
"""

import ast
from pathlib import Path

import canon

SRC = Path(canon.__file__).parent
MAX_LINES = 500

#: 本體子包（`canon/<代號>/`）。目前只有法規；新增本體時加進來。
ONTOLOGIES = {"law"}
#: 組合根：把本體接進核心的地方，可以匯入本體子包。
COMPOSITION_ROOTS = {"server.py", "sync.py", "acquire/cli.py"}


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


def _imported_ontologies(path: Path) -> set[str]:
    """這個檔案匯入的本體子包代號。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            parts = node.module.split(".")
            if parts[0] == "canon" and len(parts) > 1:
                found.add(parts[1])
            elif node.module == "canon":
                found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.split(".")[1] for alias in node.names if alias.name.startswith("canon.")
            )
    return found & ONTOLOGIES


def test_the_core_does_not_import_an_ontology() -> None:
    """依賴只往一個方向：本體（來源、解析、同步）用核心，核心不認得任何本體。"""
    leaks = {}
    for path in _modules():
        rel = path.relative_to(SRC).as_posix()
        if rel.split("/")[0] in ONTOLOGIES or rel in COMPOSITION_ROOTS:
            continue
        if imported := _imported_ontologies(path):
            leaks[rel] = sorted(imported)

    assert not leaks, (
        f"核心匯入了本體子包：{leaks}。把共用的部分放在核心，或只在組合根"
        f"（{sorted(COMPOSITION_ROOTS)}）接線。"
    )


def test_ontologies_do_not_import_each_other() -> None:
    crossing = {}
    for path in _modules():
        rel = path.relative_to(SRC).as_posix()
        home = rel.split("/")[0]
        if home in ONTOLOGIES and (others := _imported_ontologies(path) - {home}):
            crossing[rel] = sorted(others)

    assert not crossing, f"本體之間互相匯入：{crossing}。共用的部分放在核心。"
