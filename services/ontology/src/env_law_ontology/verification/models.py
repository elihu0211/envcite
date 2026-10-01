"""語言模型的介面與轉接器。

系統只知道 `Model`：有名字、有家族、給 prompt 回字串。名字記入推導與驗證紀錄；家族用來強制獨立性——
驗證者與抽取者同一家族，共同偏誤就擋不住（一致不等於正確），所以預設不採計同家族驗證者的結果。
轉接器只是把一個供應商的 API 包成 `Model`；目前只有 Anthropic，其他家族要自己寫轉接器（這正是
跨家族驗證要的，也是目前缺的）。
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Protocol


class Model(Protocol):
    @property
    def name(self) -> str:
        """記入紀錄的識別：`家族:模型`，例如 `anthropic:claude-sonnet-5-5`。"""
        ...

    @property
    def family(self) -> str:
        """模型家族：同一家族的模型共享訓練資料與偏誤，不算互相獨立。"""
        ...

    def complete(self, prompt: str) -> str: ...


def family_of(agent: str) -> str:
    """推導者或驗證者的家族：名稱 `家族:模型` 的前綴；沒有冒號的整個名稱當家族。"""
    return agent.split(":", 1)[0]


class AnthropicModel:
    """Anthropic 的 Messages API。金鑰取自環境變數 `ANTHROPIC_API_KEY`。需要 `llm` 選用依賴。"""

    family = "anthropic"

    def __init__(self, model_id: str, max_tokens: int = 4096) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._client: Any = None

    @property
    def name(self) -> str:
        return f"anthropic:{self.model_id}"

    def complete(self, prompt: str) -> str:
        if self._client is None:
            try:
                sdk = importlib.import_module("anthropic")
            except ModuleNotFoundError as e:
                raise RuntimeError("anthropic 未安裝：uv sync --extra llm") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is required")
            self._client = sdk.Anthropic()
        message = self._client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in message.content if getattr(b, "type", "") == "text")


def build_model(spec: str) -> Model:
    """`供應商:模型` → Model。沒有轉接器的供應商明說，不靜默換成別家。"""
    vendor, _, model_id = spec.partition(":")
    if vendor == "anthropic" and model_id:
        return AnthropicModel(model_id)
    raise ValueError(f"no adapter for {vendor!r}: implement Model for it (spec {spec!r})")
