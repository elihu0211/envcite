"""模型配置：角色對模型、模型對端點，寫在一個 TOML 檔（`models.toml`）。

- 角色明寫：抽取者、往返重抽者、獨立測試設計者、評審團各指名模型，不靠驗證者順序隱含。
- 家族由人宣告，不從模型名推測：託管端點的名稱可能是別名，獨立性規則依家族判定。
- 設定檔只寫金鑰位置（`api_key_env` 或相對於設定檔的 `api_key_file`）；
  金鑰檔在未忽略的 git 工作樹內則拒絕載入。
- 語意檢索的嵌入模型也在這裡宣告（`[embedding]`），沿用同一組端點；伺服器與同步排程只讀這一段，
  不需要標註管線的角色（`load_embedder`）。

不認得的鍵與指向不存在者的參照一律報錯。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from envcite_ontology.embedding import Embedder
from envcite_ontology.verification.models import AnthropicModel, ChatCompletionsModel, Model

ROLES = ("extractor", "roundtrip", "independent_test", "panel")


class ConfigError(ValueError):
    """設定檔不合：缺角色、參照不存在、不認得的鍵、缺環境變數。"""


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


def _key_from_file(where: str, name: Any, base: Path) -> str:
    path = (base / str(name)).resolve()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"{where}: cannot read the key file {path}: {e.strerror}") from e
    if not key:
        raise ConfigError(f"{where}: the key file {path} is empty")
    # check-ignore 回 1＝在工作樹內且未忽略，拒絕以免被提交；128（不在工作樹）或無 git 則放行。
    try:
        ignored = subprocess.run(
            ["git", "-C", str(path.parent), "check-ignore", "-q", str(path)],
            capture_output=True,
        ).returncode
    except OSError:
        ignored = 128
    if ignored == 1:
        raise ConfigError(
            f"{where}: the key file {path} is inside a git repository and not ignored"
        )
    return key


def _endpoint(
    name: str, table: Mapping[str, Any], env: Mapping[str, str], base_dir: Path
) -> tuple[str, str | None]:
    where = f"endpoint.{name}"
    _only(table, {"base_url", "base_url_env", "api_key_env", "api_key_file"}, where)
    if "api_key_env" in table and "api_key_file" in table:
        raise ConfigError(f"{where}: give at most one of api_key_env and api_key_file")
    if ("base_url" in table) == ("base_url_env" in table):
        raise ConfigError(f"{where}: give exactly one of base_url and base_url_env")
    base = table.get("base_url")
    if base is None:
        var = str(table["base_url_env"])
        base = env.get(var)
        if not base:
            raise ConfigError(f"{where}: environment variable {var} is not set")
    key: str | None = None
    if "api_key_file" in table:
        key = _key_from_file(where, table["api_key_file"], base_dir)
    elif (key_var := table.get("api_key_env")) is not None:
        key = env.get(str(key_var))
        if not key:
            raise ConfigError(f"{where}: environment variable {key_var} is not set")
    return str(base), key


def _model(
    name: str,
    table: Mapping[str, Any],
    endpoints: Mapping[str, tuple[str, str | None]],
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
    text: str, env: Mapping[str, str] | None = None, base_dir: Path | None = None
) -> ModelSet:
    env = os.environ if env is None else env
    base_dir = Path.cwd() if base_dir is None else base_dir
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, {"endpoint", "model", "roles", "embedding"}, "top level")
    endpoints = {
        str(n): _endpoint(str(n), t, env, base_dir) for n, t in (doc.get("endpoint") or {}).items()
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


def load(path: Path, env: Mapping[str, str] | None = None) -> ModelSet:
    return parse(path.read_text(encoding="utf-8"), env, path.resolve().parent)


def parse_embedder(
    text: str,
    env: Mapping[str, str] | None = None,
    base_dir: Path | None = None,
    *,
    timeout: float,
) -> Embedder | None:
    """`[embedding]` 宣告的嵌入模型；沒有這一段為 None（語意臂關閉）。只解析它指到的端點。"""
    env = os.environ if env is None else env
    base_dir = Path.cwd() if base_dir is None else base_dir
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, {"endpoint", "model", "roles", "embedding"}, "top level")
    table = doc.get("embedding")
    if table is None:
        return None
    _only(table, {"endpoint", "name"}, "embedding")
    name = table.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("embedding: name is required")
    endpoint = str(table.get("endpoint"))
    endpoints = doc.get("endpoint") or {}
    if endpoint not in endpoints:
        raise ConfigError(f"embedding: endpoint {endpoint!r} is not defined")
    base, key = _endpoint(endpoint, endpoints[endpoint], env, base_dir)
    return Embedder(name, base_url=base, api_key=key, timeout=timeout)


def load_embedder(
    path: Path, env: Mapping[str, str] | None = None, *, timeout: float
) -> Embedder | None:
    return parse_embedder(
        path.read_text(encoding="utf-8"), env, path.resolve().parent, timeout=timeout
    )
