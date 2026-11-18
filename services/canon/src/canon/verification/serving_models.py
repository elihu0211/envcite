"""請求路徑上用的語言模型：問答的案件事實抽取（`models.toml` 的 `[fact_extractor]`）。

標註管線的角色與其他模型段在 `config`；這裡只放服務在請求路徑上呼叫的模型，它的逾時與重試
和離線管線不同，另成一個模組讓設定的解析各管一類。
"""

import os
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from canon.settings import ModelsSettings
from canon.verification.config import (
    TOP_LEVEL,
    ConfigError,
    build_model,
    check_keys,
    keychain_lookup,
    resolve_endpoint,
)
from canon.verification.models import ChatCompletionsModel


def parse_fact_extractor(
    text: str,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> ChatCompletionsModel | None:
    """`[fact_extractor]` 指名的案件事實抽取模型（`model` 指向一個 `[model.*]`）。

    沒有這一段為 None（`ExtractFacts` 關閉）。只解析它指到的端點。服務在請求路徑上呼叫它，所以
    被指到的模型要設 `timeout` 與 `max_retries`，不要用標註管線的長逾時與重試。
    """
    env = os.environ if env is None else env
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"not valid TOML: {e}") from e
    check_keys(doc, set(TOP_LEVEL), "top level")
    table = doc.get("fact_extractor")
    if table is None:
        return None
    check_keys(table, {"model"}, "fact_extractor")
    alias = table.get("model")
    tables = doc.get("model") or {}
    if not isinstance(alias, str) or alias not in tables:
        raise ConfigError(f"fact_extractor.model: model {alias!r} is not defined")
    endpoint = str(tables[alias].get("endpoint"))
    endpoints_doc = doc.get("endpoint") or {}
    if endpoint not in endpoints_doc:
        raise ConfigError(f"model.{alias}: endpoint {endpoint!r} is not defined")
    endpoints = {endpoint: resolve_endpoint(endpoint, endpoints_doc[endpoint], env, keychain)}
    model = build_model(alias, tables[alias], endpoints)
    if not isinstance(model, ChatCompletionsModel):
        raise ConfigError("fact_extractor.model: an OpenAI-compatible chat model is required")
    return model


def load_fact_extractor(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    keychain: Callable[[str], str | None] = keychain_lookup,
) -> ChatCompletionsModel | None:
    """讀取模型配置檔的 `[fact_extractor]`，規則同 `parse_fact_extractor`。"""
    return parse_fact_extractor(path.read_text(encoding="utf-8"), env, keychain=keychain)


def fact_extractor_from_settings(settings: ModelsSettings) -> ChatCompletionsModel | None:
    """`[fact_extractor]` 的抽取模型；沒有設定檔或沒有這一段為 None。"""
    return load_fact_extractor(settings.models) if settings.models else None
