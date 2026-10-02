"""模型配置：哪個角色用哪個模型、連到哪個端點，寫在一個 TOML 檔裡（`models.example.toml`）。

為什麼不再靠命令列旗標與位置：驗證者的順序決定誰擔任往返驗證、誰設計獨立測試（`gates_for`），
那是隱含的；環境變數只有一組 `CHAT_*`，同一次跑不能連兩個端點。設定檔把兩件事說明白：

- **角色明寫**：抽取者、往返驗證的重抽者、獨立測試的設計者、評審團，各自指名一個模型。
- **家族明寫**：獨立性規則（驗證者不得與抽取者同一家族）所依的家族由人宣告，不從模型名字推測；
  託管端點的名稱可能是別名（`gpt-oss-120b` 與 `mistral-small-4` 是同一個模型），宣告時要查清楚。
- **金鑰不入檔**：只寫存放金鑰的環境變數名稱（`api_key_env`），檔案本身可以進版控與審閱。

只用標準函式庫（`tomllib`）；不認得的鍵與指向不存在者的參照一律報錯，不靜默忽略。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from env_law_ontology.verification.models import AnthropicModel, ChatCompletionsModel, Model

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


def _endpoint(
    name: str, table: Mapping[str, Any], env: Mapping[str, str]
) -> tuple[str, str | None]:
    where = f"endpoint.{name}"
    _only(table, {"base_url", "base_url_env", "api_key_env"}, where)
    if ("base_url" in table) == ("base_url_env" in table):
        raise ConfigError(f"{where}: give exactly one of base_url and base_url_env")
    base = table.get("base_url")
    if base is None:
        var = str(table["base_url_env"])
        base = env.get(var)
        if not base:
            raise ConfigError(f"{where}: environment variable {var} is not set")
    key: str | None = None
    if (key_var := table.get("api_key_env")) is not None:
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
    _only(table, {"provider", "endpoint", "name", "family", "max_tokens"}, where)
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
    extra = {"max_tokens": int(table["max_tokens"])} if "max_tokens" in table else {}
    return ChatCompletionsModel(remote, declared_family=family, base_url=base, api_key=key, **extra)


def parse(text: str, env: Mapping[str, str] | None = None) -> ModelSet:
    env = os.environ if env is None else env
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    _only(doc, {"endpoint", "model", "roles"}, "top level")
    endpoints = {str(n): _endpoint(str(n), t, env) for n, t in (doc.get("endpoint") or {}).items()}
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
    return parse(path.read_text(encoding="utf-8"), env)
