"""語言模型的介面與轉接器。

系統只知道 `Model`：有名字、有家族、給 prompt 回字串。名字記入推導與驗證紀錄；家族用來強制獨立性——
驗證者與抽取者同一家族，共同偏誤就擋不住（一致不等於正確），所以預設不採計同家族驗證者的結果。
轉接器只是把一個供應商的 API 包成 `Model`。有兩個：Anthropic 的 Messages API，與 OpenAI 相容的
Chat Completions（OpenRouter、Together、Fireworks、Groq、DeepInfra 等託管服務，或自架的 vLLM、
llama.cpp server、Ollama）——後者讓 gpt-oss-120b 這類開放權重模型，以及 Qwen、DeepSeek、Llama 等
其他家族都能當驗證者，這正是跨家族驗證要的。
"""

from __future__ import annotations

import importlib
import json
import os
import urllib.error
import urllib.request
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


class ChatCompletionsModel:
    """OpenAI 相容的 Chat Completions 端點，只用標準函式庫。

    模型以 `供應商/模型` 的 slug 指定，例如 `openai/gpt-oss-120b`、`qwen/qwen3-235b-a22b`：斜線前的
    供應商就是家族（`family`），所以不同家族的開放權重模型能以同一個轉接器當驗證者。端點取自環境
    變數 `CHAT_BASE_URL`（必填，沒有預設：不把法規文字悄悄送到沒指定的第三方）、金鑰取自
    `CHAT_API_KEY`（自架的伺服器可以沒有）。gpt-oss 一類的推理模型會把思考也算進輸出長度，所以
    `max_tokens` 預設較大；只取 `message.content`，思考欄位不採用。slug 沒有斜線時家族記為
    `unknown`：兩個這樣的模型會被當成同一家族，只會讓閘門更保守，不會更寬鬆。

    送出的模型識別預設是整個 slug（OpenRouter 這類閘道的寫法）；自架或自訂的端點常只認斜線後的
    名字（`gpt-oss-120b`），設環境變數 `CHAT_MODEL_FORMAT=bare` 就只送斜線後的部分。家族與名字
    仍取自 slug，所以 `chat:openai/gpt-oss-120b` 與 `chat:google/gemma-4-31b` 在只認名字的端點上
    也記成不同家族。
    """

    def __init__(self, slug: str, max_tokens: int = 8192, timeout: float = 300.0) -> None:
        self.slug = slug
        self.max_tokens = max_tokens
        self.timeout = timeout

    @property
    def family(self) -> str:
        vendor, sep, _ = self.slug.partition("/")
        return vendor if sep and vendor else "unknown"

    @property
    def name(self) -> str:
        return f"{self.family}:{self.slug.partition('/')[2] or self.slug}"

    @property
    def request_model(self) -> str:
        bare = os.environ.get("CHAT_MODEL_FORMAT", "slug") == "bare"
        return self.slug.partition("/")[2] if bare and "/" in self.slug else self.slug

    def complete(self, prompt: str) -> str:
        base = os.environ.get("CHAT_BASE_URL", "").rstrip("/")
        if not base:
            raise RuntimeError(
                "CHAT_BASE_URL is required (an OpenAI-compatible endpoint, e.g. .../v1)"
            )
        headers = {"Content-Type": "application/json"}
        if key := os.environ.get("CHAT_API_KEY"):
            headers["Authorization"] = f"Bearer {key}"
        body = json.dumps(
            {
                "model": self.request_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens,
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(f"{base}/chat/completions", body, headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{self.name}: HTTP {e.code}: {e.read()[:200]!r}") from e
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content:
            raise RuntimeError(
                f"{self.name}: empty content (a reasoning model may have used all max_tokens)"
            )
        return content


def build_model(spec: str) -> Model:
    """`anthropic:<模型>` 或 `chat:<供應商/模型>` → Model。其餘明說，不靜默換成別家。"""
    vendor, _, model_id = spec.partition(":")
    if vendor == "anthropic" and model_id:
        return AnthropicModel(model_id)
    if vendor == "chat" and model_id:
        return ChatCompletionsModel(model_id)
    raise ValueError(f"no adapter for {vendor!r}: implement Model for it (spec {spec!r})")
