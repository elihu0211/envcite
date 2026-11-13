"""模型配置：角色對模型、模型對端點，寫在一個 TOML 檔（`models.toml`）。

- 角色明寫：抽取者、往返重抽者、獨立測試設計者、評審團各指名模型，不靠驗證者順序隱含。
- 家族由人宣告，不從模型名推測：託管端點的名稱可能是別名，獨立性規則依家族判定。
- 設定檔不寫金鑰與金鑰名：名稱由端點名推導（`secret_name`），即 `ENVCITE_<端點名大寫>_API_KEY`，
  依序從同名環境變數、作業系統鑰匙圈同名 service 解析（`resolve_secret`）。
- 語意檢索的嵌入模型也在這裡宣告（`[embedding]`），沿用同一組端點；伺服器與同步排程只讀這一段，
  不需要標註管線的角色（`load_embedder`）。查詢時的精排模型同理（`[reranker]`，`load_reranker`）。
- 黃金測試集的相關性判定者也在這裡（`[relevance]`，`load_judges`）：至少兩個、彼此不同家族，且不與
  受測路徑上的任何家族相同（`system_families`）。

不認得的鍵與指向不存在者的參照一律報錯。
"""

import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from envcite_ontology.embedding import Embedder
from envcite_ontology.reranking import Reranker
from envcite_ontology.settings import ModelsSettings
from envcite_ontology.verification.models import AnthropicModel, ChatCompletionsModel, Model

ROLES = ("extractor", "roundtrip", "independent_test", "panel")
TOP_LEVEL = ("endpoint", "model", "roles", "embedding", "reranker", "relevance")


class ConfigError(ValueError):
    """設定檔不合：缺角色、參照不存在、不認得的鍵、缺金鑰。"""


@dataclass(frozen=True)
class ModelSet:
    extractor: Model
    roundtrip: Model
    independent_test: Model
    panel: tuple[Model, ...]

    @property
    def verifiers(self) -> tuple[Model, ...]:
        """所有驗證者，不重複，依角色出現的順序。"""
        seen: dict[str, Model] = {}
        for m in (self.roundtrip, self.independent_test, *self.panel):
            seen.setdefault(m.name, m)
        return tuple(seen.values())

    @property
    def everyone(self) -> tuple[Model, ...]:
        return (self.extractor, *self.verifiers)


def _only(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = set(table) - allowed
    if extra:
        raise ConfigError(f"{where}: unknown keys {sorted(extra)}")


KEYCHAIN_TIMEOUT = 10.0

# 子行程的指令表，依 sys.platform 選；金鑰只經標準輸出回到本行程，不進命令列。
# Windows 與沒有鑰匙圈 CLI 的環境沒有這一項，只剩環境變數。
_KEYCHAIN_COMMANDS: Mapping[str, Callable[[str], list[str]]] = {
    "darwin": lambda name: ["security", "find-generic-password", "-s", name, "-w"],
    "linux": lambda name: ["secret-tool", "lookup", "service", name],
}


def keychain_lookup(
    name: str, *, platform: str | None = None, timeout: float | None = None
) -> str | None:
    """作業系統鑰匙圈裡 service 為 `name` 的通用密碼；沒有、取不到、逾時或沒有鑰匙圈為 None。

    鎖住的鑰匙圈會跳出授權視窗，所以子行程有逾時；逾時視同取不到，不拋出子行程的任何輸出。
    """
    command = _KEYCHAIN_COMMANDS.get(platform or sys.platform)
    if command is None:
        return None
    try:
        done = subprocess.run(
            command(name),
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT if timeout is None else timeout,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def resolve_secret(
    name: str,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> str:
    """名稱對應的機密：同名環境變數優先，其次鑰匙圈同名 service；都沒有則 `ConfigError`。

    錯誤訊息只列名稱與找過的位置，不含任何值。
    """
    env = os.environ if env is None else env
    if value := env.get(name):
        return value
    if value := keychain(name):
        return value
    raise ConfigError(
        f"secret {name} not found: set the environment variable {name}, "
        f"or store a keychain generic password with service {name}"
    )


# 端點名要能轉成環境變數名：小寫字母開頭，其後小寫字母、數字、底線。不收大寫與連字號，
# 避免 `llm` 與 `LLM` 推導出同一把金鑰、或推導出 shell 不能設的名稱。
_ENDPOINT_NAME = re.compile(r"[a-z][a-z0-9_]*")


def secret_name(endpoint: str) -> str:
    """端點的金鑰名稱：`ENVCITE_<端點名大寫>_API_KEY`。端點名不合法則 `ConfigError`。"""
    if not _ENDPOINT_NAME.fullmatch(endpoint):
        raise ConfigError(
            f"endpoint.{endpoint}: the endpoint name must match [a-z][a-z0-9_]* "
            "(it becomes part of the secret name ENVCITE_<NAME>_API_KEY)"
        )
    return f"ENVCITE_{endpoint.upper()}_API_KEY"


def _endpoint(
    name: str,
    table: Mapping[str, Any],
    env: Mapping[str, str],
    keychain: Callable[[str], str | None],
) -> tuple[str, str]:
    where = f"endpoint.{name}"
    secret = secret_name(name)
    _only(table, {"base_url"}, where)
    base = table.get("base_url")
    if not isinstance(base, str) or not base:
        raise ConfigError(f"{where}: base_url is required")
    try:
        return base, resolve_secret(secret, env, keychain=keychain)
    except ConfigError as e:
        raise ConfigError(f"{where}: {e}") from None


def _model(
    name: str,
    table: Mapping[str, Any],
    endpoints: Mapping[str, tuple[str, str]],
) -> Model:
    where = f"model.{name}"
    _only(
        table,
        {
            "provider",
            "endpoint",
            "name",
            "family",
            "max_tokens",
            "max_retries",
            "constrained_extraction",
        },
        where,
    )
    remote = table.get("name")
    if not isinstance(remote, str) or not remote:
        raise ConfigError(f"{where}: name is required")
    provider = table.get("provider", "chat")
    if provider == "anthropic":
        if "endpoint" in table or "family" in table:
            raise ConfigError(f"{where}: an anthropic model has no endpoint or family to declare")
        return AnthropicModel(remote)
    if provider != "chat":
        raise ConfigError(f"{where}: unknown provider {provider!r} (chat or anthropic)")
    endpoint = str(table.get("endpoint"))
    if endpoint not in endpoints:
        raise ConfigError(f"{where}: endpoint {endpoint!r} is not defined")
    family = table.get("family")
    if not isinstance(family, str) or not family:
        raise ConfigError(f"{where}: family is required (it is what independence is judged on)")
    base, key = endpoints[endpoint]
    extra: dict[str, Any] = {k: int(table[k]) for k in ("max_tokens", "max_retries") if k in table}
    constrained = table.get("constrained_extraction", False)
    if not isinstance(constrained, bool):
        raise ConfigError(f"{where}: constrained_extraction must be true or false")
    return ChatCompletionsModel(
        remote,
        family=family,
        base_url=base,
        api_key=key,
        constrained_extraction=constrained,
        **extra,
    )


def parse(
    text: str,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> ModelSet:
    env = os.environ if env is None else env
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, set(TOP_LEVEL), "top level")
    endpoints = {
        str(n): _endpoint(str(n), t, env, keychain) for n, t in (doc.get("endpoint") or {}).items()
    }
    models = {str(n): _model(str(n), t, endpoints) for n, t in (doc.get("model") or {}).items()}
    roles = doc.get("roles") or {}
    _only(roles, set(ROLES), "roles")
    missing = [r for r in ROLES if r not in roles]
    if missing:
        raise ConfigError(f"roles: missing {missing}")

    def ref(alias: Any, where: str) -> Model:
        if alias not in models:
            raise ConfigError(f"roles.{where}: model {alias!r} is not defined")
        return models[alias]

    panel = roles["panel"]
    if not isinstance(panel, list) or not panel:
        raise ConfigError("roles.panel: a non-empty list of model names")
    return ModelSet(
        extractor=ref(roles["extractor"], "extractor"),
        roundtrip=ref(roles["roundtrip"], "roundtrip"),
        independent_test=ref(roles["independent_test"], "independent_test"),
        panel=tuple(ref(a, "panel") for a in panel),
    )


def load(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> ModelSet:
    return parse(path.read_text(encoding="utf-8"), env, keychain=keychain)


def _declared(
    text: str,
    section: str,
    env: Mapping[str, str] | None,
    keychain: Callable[[str], str | None],
) -> tuple[str, str, str | None] | None:
    """`[<section>]`（`endpoint`、`name`）宣告的模型：（名稱，端點網址，金鑰）；沒有這一段為 None。

    只解析它指到的端點。
    """
    env = os.environ if env is None else env
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, set(TOP_LEVEL), "top level")
    table = doc.get(section)
    if table is None:
        return None
    _only(table, {"endpoint", "name"}, section)
    name = table.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{section}: name is required")
    endpoint = str(table.get("endpoint"))
    endpoints = doc.get("endpoint") or {}
    if endpoint not in endpoints:
        raise ConfigError(f"{section}: endpoint {endpoint!r} is not defined")
    base, key = _endpoint(endpoint, endpoints[endpoint], env, keychain)
    return name, base, key


def parse_embedder(
    text: str,
    env: Mapping[str, str] | None = None,
    *,
    timeout: float,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Embedder | None:
    """`[embedding]` 宣告的嵌入模型；沒有這一段為 None（語意臂關閉）。只解析它指到的端點。"""
    declared = _declared(text, "embedding", env, keychain)
    if declared is None:
        return None
    name, base, key = declared
    return Embedder(name, base_url=base, api_key=key, timeout=timeout)


def parse_reranker(
    text: str,
    env: Mapping[str, str] | None = None,
    *,
    timeout: float,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Reranker | None:
    """`[reranker]` 宣告的精排模型；沒有這一段為 None（不精排）。只解析它指到的端點。"""
    declared = _declared(text, "reranker", env, keychain)
    if declared is None:
        return None
    name, base, key = declared
    return Reranker(name, base_url=base, api_key=key, timeout=timeout)


def load_embedder(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    timeout: float,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Embedder | None:
    return parse_embedder(path.read_text(encoding="utf-8"), env, timeout=timeout, keychain=keychain)


def load_reranker(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    timeout: float,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Reranker | None:
    return parse_reranker(path.read_text(encoding="utf-8"), env, timeout=timeout, keychain=keychain)


def embedder_from_settings(settings: ModelsSettings, *, timeout: float) -> Embedder | None:
    """`[embedding]` 的嵌入模型；沒有設定檔或沒有這一段為 None，檢索只用字面臂。"""
    return load_embedder(settings.models, timeout=timeout) if settings.models else None


def reranker_from_settings(settings: ModelsSettings, *, timeout: float) -> Reranker | None:
    """`[reranker]` 的精排模型；沒有設定檔或沒有這一段為 None，檢索不精排。"""
    return load_reranker(settings.models, timeout=timeout) if settings.models else None


@dataclass(frozen=True)
class Judges:
    """相關性判定者與它們必須避開的受測系統家族。"""

    models: tuple[Model, ...]
    system_families: frozenset[str]


def parse_judges(
    text: str,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Judges:
    """`[relevance]` 指名的判定者。判定者少於兩個、彼此同家族、或與受測系統同家族都報錯。

    受測系統的家族只有 `system_families`（受測路徑上的模型：嵌入與作答）；標註管線的角色是離線的，
    不算受測系統，它們的模型可以當判定者。
    """
    env = os.environ if env is None else env
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, set(TOP_LEVEL), "top level")
    table = doc.get("relevance")
    if table is None:
        raise ConfigError("relevance: no judges declared")
    _only(table, {"judges", "system_families"}, "relevance")
    names = table.get("judges")
    if not isinstance(names, list) or len(names) < 2:
        raise ConfigError("relevance.judges: at least two model names")
    declared = table.get("system_families") or []
    if not isinstance(declared, list) or not all(isinstance(f, str) and f for f in declared):
        raise ConfigError("relevance.system_families: a list of family names")
    tables = doc.get("model") or {}
    endpoints_doc = doc.get("endpoint") or {}
    wanted = {str(tables[n].get("endpoint")) for n in names if n in tables}
    endpoints = {
        str(n): _endpoint(str(n), t, env, keychain)
        for n, t in endpoints_doc.items()
        if str(n) in wanted
    }
    for n in names:
        if n not in tables:
            raise ConfigError(f"relevance.judges: model {n!r} is not defined")
    judges = tuple(_model(str(n), tables[n], endpoints) for n in names)
    system = set(declared)
    families = [j.family for j in judges]
    if len(set(families)) != len(families):
        raise ConfigError(f"relevance.judges: two judges share a family {families}")
    clash = sorted(set(families) & system)
    if clash:
        raise ConfigError(f"relevance.judges: family {clash} is part of the system under test")
    return Judges(judges, frozenset(system))


def load_judges(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> Judges:
    return parse_judges(path.read_text(encoding="utf-8"), env, keychain=keychain)
