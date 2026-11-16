"""`canon <子指令>`：服務所有入口的單一指令，`python -m canon` 等價。

子指令名稱是與部署庫的契約。每個子指令是一個模組的 `main()`，自己解析其餘的參數；
這裡只依名稱分派，並延後匯入：`canon --help` 不必載入驗證管線與 clingo。
"""

import importlib
import sys
from collections.abc import Callable, Sequence

#: 子指令 → （模組，一句話說明）。新增入口在此登記。
SUBCOMMANDS: dict[str, tuple[str, str]] = {
    "serve": ("canon.server", "gRPC 伺服器（長駐）"),
    "migrate": ("canon.migrate", "套用 migrations/*.sql"),
    "sync": ("canon.sync", "取得、匯入、授權抽取、檢索投影（排程）"),
    "acquire": ("canon.acquire.cli", "只取得來源的快照"),
    "import": ("canon.law.import_cli", "快照寫入語料"),
    "history": ("canon.law.history_sync.cli", "法規的歷史版本（排程）"),
    "guidance": ("canon.law.guidance_sync.cli", "行政規則：函釋與公報（排程、載入）"),
    "ruling": ("canon.law.ruling_sync", "違憲失效的裁判（取得、載入）"),
    "judgment": ("canon.law.judgment_sync", "行政法院裁判：異動清單與全文（每日 0 時至 6 時）"),
    "pipeline": ("canon.verification.run", "標註管線：抽取、驗證、接受（排程）"),
    "annotate": ("canon.annotate", "載入一份人寫的標註 JSON"),
    "verify": ("canon.verification.cli", "只跑確定性閘門的驗證"),
}

EXIT_USAGE = 2


def _usage() -> str:
    width = max(map(len, SUBCOMMANDS))
    lines = [f"  {name.ljust(width)}  {summary}" for name, (_, summary) in SUBCOMMANDS.items()]
    return "\n".join(["usage: canon <子指令> [參數]", "", "子指令：", *lines])


def main(argv: Sequence[str] | None = None) -> None:
    """依第一個參數分派；其餘參數原樣交給該子指令。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_usage(), file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    name, *rest = args
    if name in {"-h", "--help"}:
        print(_usage())
        raise SystemExit(0)
    if name not in SUBCOMMANDS:
        print(f"canon: 沒有子指令 {name!r}\n\n{_usage()}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    module, _ = SUBCOMMANDS[name]
    sys.argv = [f"canon {name}", *rest]
    entry: Callable[[], None] = importlib.import_module(module).main
    entry()
