"""結構守門：模組不得長成巨石，套件不再匯出，邏輯不直接讀系統時鐘，核心不匯入來源子包。

單一模組超過 `MAX_LINES` 行，就是在一個檔案裡放了不只一個職責：把職責拆到各自的模組，
不要調高上限。
"""

import ast
from pathlib import Path

import canon

SRC = Path(canon.__file__).parent
MAX_LINES = 500

#: 來源子包（`canon/<代號>/`）。目前只有法規；新增來源時加進來。
SOURCES = {"law"}
#: 組合根：把來源接進核心的地方，可以匯入來源子包。
COMPOSITION_ROOTS = {"cli.py", "server.py", "sync.py", "acquire/cli.py"}


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


def test_a_package_init_holds_only_its_docstring() -> None:
    """消費端從定義處匯入：`__init__` 再匯出會讓匯入一個名字就載入整個套件，也讓定義處有兩個。"""
    busy = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if path.name == "__init__.py"
        and any(not _is_docstring(node) for node in ast.parse(path.read_text()).body)
    ]

    assert not busy, f"`__init__.py` 只放套件說明：{busy}。名稱放在子模組，消費端從那裡匯入。"


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


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
        if path.name != "clock.py"
        and (lines := _clock_reads(path))
        and (rel := path.relative_to(SRC).as_posix())
    }

    assert not reads, f"直接讀系統時鐘：{reads}。改收注入的 `clock.Today`（預設 `date.today`）。"


#: 設定日誌的地方：分派器統一設定，長駐的伺服器另需帶記錄器名稱的格式。
LOGGING_CONFIGURERS = {"cli.py", "server.py"}


def _configures_logging(path: Path) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "basicConfig"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )


def test_logging_is_configured_where_the_process_starts_and_nowhere_else() -> None:
    """子指令各自 `basicConfig` 會讓格式各寫一份；改格式要動十幾個檔，也容易漏。"""
    scattered = [
        rel
        for path in _modules()
        if (rel := path.relative_to(SRC).as_posix()) not in LOGGING_CONFIGURERS
        and _configures_logging(path)
    ]

    assert not scattered, f"在入口以外設定日誌：{scattered}。日誌由 `cli.main` 統一設定。"


def _hand_written_exit_codes(path: Path) -> list[int]:
    """自訂的結束碼：`EXIT_*` 常數，或 `raise SystemExit(<整數>)`。"""
    nodes = list(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))
    constants = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id.startswith("EXIT_") for t in node.targets)
    ]
    literals = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SystemExit"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, int)
    ]
    return [*constants, *literals]


def test_exit_codes_come_from_exit_code_and_not_from_local_numbers() -> None:
    """數值是與部署庫的契約：入口用 `envcite_jobkit.ExitCode` 或 `exit_codes.exit_code(error)`。"""
    hand_written = {
        rel: lines
        for path in _modules()
        if (lines := _hand_written_exit_codes(path)) and (rel := path.relative_to(SRC).as_posix())
    }

    assert not hand_written, (
        f"自訂的結束碼：{hand_written}。改用 `ExitCode`，失敗用 `exit_codes.fail`／`exit_code`。"
    )


def _imported_sources(path: Path) -> set[str]:
    """這個檔案匯入的來源子包代號。"""
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
    return found & SOURCES


def test_the_core_does_not_import_a_source() -> None:
    """依賴只往一個方向：來源（取得、解析、同步）用核心，核心不認得任何來源。"""
    leaks = {}
    for path in _modules():
        rel = path.relative_to(SRC).as_posix()
        if rel.split("/")[0] in SOURCES or rel in COMPOSITION_ROOTS:
            continue
        if imported := _imported_sources(path):
            leaks[rel] = sorted(imported)

    assert not leaks, (
        f"核心匯入了來源子包：{leaks}。把共用的部分放在核心，或只在組合根"
        f"（{sorted(COMPOSITION_ROOTS)}）接線。"
    )


def test_sources_do_not_import_each_other() -> None:
    crossing = {}
    for path in _modules():
        rel = path.relative_to(SRC).as_posix()
        home = rel.split("/")[0]
        if home in SOURCES and (others := _imported_sources(path) - {home}):
            crossing[rel] = sorted(others)

    assert not crossing, f"來源之間互相匯入：{crossing}。共用的部分放在核心。"
