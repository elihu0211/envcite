"""語言模型的介面 `Model`（名字、家族、prompt → 字串）與轉接器。

名字記入推導與驗證紀錄；家族用來強制獨立性，同家族驗證者預設不採計。
轉接器有兩個：Anthropic Messages API，與 OpenAI 相容的 Chat Completions（託管服務或自架），
後者讓開放權重的其他家族也能當驗證者。
"""

import importlib
import os
import random
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from envcite_ontology.http import HttpStatus, TransportError, endpoint, post_json


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

    `model_id` 是送給端點的模型識別，原樣送出；`family` 由人宣告（獨立性規則所依），
    `base_url` 必填、無預設：不把法規文字悄悄送到未指定的第三方；`api_key` 自架可無。
    名字是 `家族:模型識別`。推理模型的思考計入輸出長度，故 `max_tokens` 預設較大；
    只取 `message.content`。
    """

    def __init__(
        self,
        model_id: str,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        *,
        family: str,
        base_url: str,
        api_key: str | None = None,
        max_retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        constrained_extraction: bool = False,
    ) -> None:
        self.model_id = model_id
        #: 陳述抽取是否對端點加解碼期約束（JSON Schema）；預設關，即現行行為。只影響抽取陳述的
        #: 呼叫，驗證者與用語定義抽取不受影響。
        self.constrained_extraction = constrained_extraction
        #: 累計的詞元用量，取自回應的 `usage`；端點不回報則維持 0
        self.tokens = {"prompt": 0, "completion": 0}
        self.family = family
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._base_url = base_url
        self._api_key = api_key
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"ChatCompletionsModel({self.name!r})"  # 不印金鑰

    @property
    def name(self) -> str:
        return f"{self.family}:{self.model_id}"

    def complete(self, prompt: str, *, response_format: Mapping[str, Any] | None = None) -> str:
        request_body: dict[str, Any] = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if response_format is not None:
            request_body["response_format"] = dict(response_format)
        payload = self._post(request_body)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self.tokens["prompt"] += int(usage.get("prompt_tokens") or 0)
            self.tokens["completion"] += int(usage.get("completion_tokens") or 0)
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content:
            raise ModelError(
                f"{self.name}: empty content (a reasoning model may have used all max_tokens)"
            )
        return content

    def _post(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """送出請求。端點問題（408／429／5xx 與連線失敗）退避重試，其餘狀態直接報。"""
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        for attempt in range(self.max_retries + 1):
            try:
                payload = post_json(url, body, api_key=self._api_key, timeout=self.timeout)
            except HttpStatus as e:
                detail = f"{self.name}: HTTP {e.status}: {e.body[:200]!r}"
                retry_after = e.headers.get("Retry-After") if e.headers is not None else None
                if not endpoint(e) or attempt == self.max_retries:
                    raise ModelError(detail) from e
                wait = self._wait(attempt, retry_after)
            except TransportError as e:
                if attempt == self.max_retries:
                    raise ModelError(f"{self.name}: {e}") from e
                wait = self._wait(attempt, None)
            else:
                if not isinstance(payload, dict):
                    raise ModelError(f"{self.name}: the reply is not a JSON object")
                return payload
            self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None) -> float:
        """退避秒數：整數的 Retry-After 優先（上限一分鐘），否則指數加抖動。"""
        if retry_after is not None and retry_after.strip().isdigit():
            return float(min(int(retry_after), 60))
        return float(self.backoff * 2**attempt * (0.5 + random.random() / 2))
