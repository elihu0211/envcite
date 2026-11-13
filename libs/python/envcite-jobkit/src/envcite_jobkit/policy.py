"""宣告式政策：所有門檻都來自設定檔或環境變數，不寫死在程式。"""

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any, Literal

OpenAction = Literal["exit", "wait"]


def _optional_float(raw: str) -> float | None:
    return float(raw)


def _optional_int(raw: str) -> int | None:
    return int(raw)


def _open_action(raw: str) -> OpenAction:
    if raw == "exit":
        return "exit"
    if raw == "wait":
        return "wait"
    raise ValueError(f"open_action 必須是 exit 或 wait：{raw!r}")


_PARSERS: dict[str, Callable[[str], Any]] = {
    "name": str,
    "failure_threshold": float,
    "min_calls": int,
    "window_seconds": float,
    "open_seconds": float,
    "half_open_probes": int,
    "open_action": _open_action,
    "max_attempts": int,
    "backoff_base_seconds": float,
    "backoff_max_seconds": float,
    "budget_seconds": _optional_float,
    "budget_items": _optional_int,
    "heartbeat_path": str,
    "heartbeat_min_interval_seconds": float,
    "dead_letter_table": str,
    "dead_letter_path": str,
    "max_dead_letters": _optional_int,
}


@dataclass(frozen=True)
class Policy:
    # Job 名稱：預設心跳檔名、日誌與指標的識別。
    name: str = "job"

    # 斷路器：視窗內失敗率達門檻（且至少 min_calls 次）就開啟。
    failure_threshold: float = 0.5
    min_calls: int = 10
    window_seconds: float = 60.0
    open_seconds: float = 300.0
    half_open_probes: int = 3
    # exit：開啟即以 TEMPFAIL 結束，交給 K8s／排程器稍後重試；wait：就地等到半開再探測。
    open_action: OpenAction = "exit"

    # 單項的端點錯誤重試（退避加抖動）。
    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0

    # 預算：用完以 OK 結束，剩餘留給下次。None 為不限。
    budget_seconds: float | None = None
    budget_items: int | None = None

    # 心跳檔；None 時用 /tmp/<name>.heartbeat。
    heartbeat_path: str | None = None
    heartbeat_min_interval_seconds: float = 0.0

    # 隔離：資料庫實作的表名；檔案實作的路徑。隔離超過 max_dead_letters 視為資料不合預期。
    dead_letter_table: str = "jobkit_dead_letter"
    dead_letter_path: str | None = None
    max_dead_letters: int | None = None

    def __post_init__(self) -> None:
        if not 0 < self.failure_threshold <= 1:
            raise ValueError("failure_threshold 必須在 (0, 1]")
        for name in ("min_calls", "half_open_probes", "max_attempts"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 必須 >= 1")
        for name in (
            "window_seconds",
            "open_seconds",
            "backoff_base_seconds",
            "backoff_max_seconds",
            "heartbeat_min_interval_seconds",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不可為負")
        if self.budget_seconds is not None and self.budget_seconds < 0:
            raise ValueError("budget_seconds 不可為負")
        if self.budget_items is not None and self.budget_items < 0:
            raise ValueError("budget_items 不可為負")

    @property
    def resolved_heartbeat_path(self) -> str:
        return self.heartbeat_path or f"/tmp/{self.name}.heartbeat"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "ENVCITE_JOBKIT_",
        base: Policy | None = None,
    ) -> Policy:
        """`<prefix><欄位名大寫>`，如 `ENVCITE_JOBKIT_OPEN_SECONDS`。空字串視為未設定。"""
        env = os.environ if environ is None else environ
        values: dict[str, Any] = {}
        for f in fields(cls):
            raw = env.get(prefix + f.name.upper())
            if raw:
                values[f.name] = _PARSERS[f.name](raw)
        return replace(base or cls(), **values)

    @classmethod
    def from_file(cls, path: str | os.PathLike[str], *, base: Policy | None = None) -> Policy:
        """TOML；欄位在頂層或 `[jobkit]` 表。未知欄位報錯，避免拼錯而安靜失效。"""
        with open(path, "rb") as f:
            data = tomllib.load(f)
        data = data.get("jobkit", data)
        unknown = set(data) - set(_PARSERS)
        if unknown:
            raise ValueError(f"未知的政策欄位：{sorted(unknown)}")
        return replace(base or cls(), **data)

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str] | None = None,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = "ENVCITE_JOBKIT_",
        defaults: Policy | None = None,
    ) -> Policy:
        """優先序：環境變數 > 設定檔 > `defaults` > 內建預設。"""
        policy = defaults or cls()
        if path is not None:
            policy = cls.from_file(path, base=policy)
        return cls.from_env(environ, prefix=prefix, base=policy)
