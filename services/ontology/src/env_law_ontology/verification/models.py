"""語言模型的介面 `Model`（名字、家族、prompt → 字串）與轉接器。

名字記入推導與驗證紀錄；家族用來強制獨立性，同家族驗證者預設不採計。
轉接器有兩個：Anthropic Messages API，與 OpenAI 相容的 Chat Completions（託管服務或自架），
後者讓開放權重的其他家族也能當驗證者。
"""

from __future__ import annotations

import importlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.client import HTTPException
from typing import Any, Protocol

#: 值得重試的 HTTP 狀態（暫時性錯誤）；其餘 4xx 重試不會好，直接報。
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ModelError(RuntimeError):
    """呼叫模型失敗（含重試用盡）；抽取與閘門視為這次沒有答案，不中斷管線。"""


class Model(Protocol):
    @property
    def name(self) -> str:
        """記入紀錄的識別 `家族:模型`，例如 `anthropic:claude-sonnet-5-5`。"""
        ...

    @property
    def family(self) -> str:
        """同家族的模型共享訓練資料與偏誤，不算互相獨立。"""
        ...

    def complete(self, prompt: str) -> str: ...


def family_of(agent: str) -> str:
    """名稱 `家族:模型` 的前綴；沒有冒號則整個名稱當家族。"""
    return agent.split(":", 1)[0]


class AnthropicModel:
    """Anthropic Messages API；金鑰取自 `ANTHROPIC_API_KEY`，需 `llm` 選用依賴。"""

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

    模型以 `供應商/模型` slug 指定（`openai/gpt-oss-120b`），斜線前的供應商即家族；無斜線時家族為
    `unknown`，多個這樣的模型視為同家族，只會更保守。
    端點取自 `CHAT_BASE_URL`（必填、無預設：不把法規文字悄悄送到未指定的第三方），金鑰取自
    `CHAT_API_KEY`（自架可無）。推理模型的思考計入輸出長度，故 `max_tokens` 預設較大；
    只取 `message.content`。
    預設送整個 slug；端點只認斜線後的名字時設 `CHAT_MODEL_FORMAT=bare`，家族與名字仍取自 slug。
    """

    def __init__(
        self,
        slug: str,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        *,
        declared_family: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`declared_family`、`base_url`、`api_key` 由設定檔（`config.py`）給時，`slug` 原樣送出；
        不給（命令列 `chat:` 規格）則退回 slug 與環境變數。"""
        self.slug = slug
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._declared_family = declared_family
        self._base_url = base_url
        self._api_key = api_key
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"ChatCompletionsModel({self.name!r})"  # 不印金鑰

    @property
    def family(self) -> str:
        if self._declared_family is not None:
            return self._declared_family
        vendor, sep, _ = self.slug.partition("/")
        return vendor if sep and vendor else "unknown"

    @property
    def name(self) -> str:
        if self._declared_family is not None:
            return f"{self._declared_family}:{self.slug}"
        return f"{self.family}:{self.slug.partition('/')[2] or self.slug}"

    @property
    def request_model(self) -> str:
        if self._declared_family is not None:
            return self.slug
        bare = os.environ.get("CHAT_MODEL_FORMAT", "slug") == "bare"
        return self.slug.partition("/")[2] if bare and "/" in self.slug else self.slug

    def complete(self, prompt: str) -> str:
        base = (self._base_url or os.environ.get("CHAT_BASE_URL", "")).rstrip("/")
        if not base:
            raise RuntimeError(
                "CHAT_BASE_URL is required (an OpenAI-compatible endpoint, e.g. .../v1)"
            )
        headers = {"Content-Type": "application/json"}
        if key := (self._api_key or os.environ.get("CHAT_API_KEY")):
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
        payload = self._post(request)
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content:
            raise ModelError(
                f"{self.name}: empty content (a reasoning model may have used all max_tokens)"
            )
        return content

    def _post(self, request: urllib.request.Request) -> dict[str, Any]:
        """送出請求；暫時性失敗以指數退避重試，其餘直接報。"""
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, dict):
                    raise ModelError(f"{self.name}: the reply is not a JSON object")
                return payload
            except urllib.error.HTTPError as e:
                detail = f"{self.name}: HTTP {e.code}: {e.read()[:200]!r}"
                if e.code not in RETRYABLE_STATUS or attempt == self.max_retries:
                    raise ModelError(detail) from e
                wait = self._wait(attempt, e.headers.get("Retry-After"))
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                HTTPException,
                json.JSONDecodeError,
            ) as e:
                if attempt == self.max_retries:
                    raise ModelError(f"{self.name}: {type(e).__name__}: {e}") from e
                wait = self._wait(attempt, None)
            self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None) -> float:
        """退避秒數：整數的 Retry-After 優先（上限一分鐘），否則指數加抖動。"""
        if retry_after is not None and retry_after.strip().isdigit():
            return float(min(int(retry_after), 60))
        return float(self.backoff * 2**attempt * (0.5 + random.random() / 2))


def build_model(spec: str) -> Model:
    """`anthropic:<模型>` 或 `chat:<供應商/模型>` → Model；其餘報錯，不靜默換成別家。"""
    vendor, _, model_id = spec.partition(":")
    if vendor == "anthropic" and model_id:
        return AnthropicModel(model_id)
    if vendor == "chat" and model_id:
        return ChatCompletionsModel(model_id)
    raise ValueError(f"no adapter for {vendor!r}: implement Model for it (spec {spec!r})")
