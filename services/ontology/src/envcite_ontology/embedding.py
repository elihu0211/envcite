"""嵌入模型：OpenAI 相容的 `/embeddings` 端點，只用標準函式庫（同 `verification.models`）。

語意臂在兩處用它：投影時為段落補嵌入（可重試、批次），查詢時為問題取向量（逾時短、不重試，
取不到就只用字面臂）。端點與模型宣告在 `models.toml` 的 `[embedding]`（`verification.config`），
沒有預設值：不把法規文字或客戶的問題悄悄送到未指定的第三方。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from http.client import HTTPException

from envcite_ontology.endpoint import json_headers
from envcite_ontology.settings import ModelsSettings

log = logging.getLogger(__name__)

#: retrieval.passage.embedding 的維度；換維度不同的模型要另一支遷移。
DIMENSION = 1024


class EmbeddingError(RuntimeError):
    """端點沒有給出可用的向量：連不上、逾時、回應形狀不對、維度不符。"""


class Embedder:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return f"Embedder({self.model!r})"  # 不印金鑰

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """依序回傳每段文字的向量。"""
        if not texts:
            return []
        headers = json_headers(self._api_key)
        body = json.dumps({"model": self.model, "input": list(texts)}).encode()
        request = urllib.request.Request(
            f"{self._base_url}/embeddings", body, headers, method="POST"
        )
        data = self._post(request).get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingError(f"{self.model}: expected {len(texts)} embeddings")
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0):
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != DIMENSION:
                raise EmbeddingError(f"{self.model}: an embedding is not {DIMENSION} numbers")
            vectors.append([float(x) for x in vector])
        return vectors

    def _post(self, request: urllib.request.Request) -> dict[str, object]:
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, dict):
                    raise EmbeddingError(f"{self.model}: the reply is not a JSON object")
                return payload
            except urllib.error.HTTPError as e:
                if e.code not in (408, 429, 500, 502, 503, 504) or attempt == self.max_retries:
                    raise EmbeddingError(f"{self.model}: HTTP {e.code}") from e
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                HTTPException,
                json.JSONDecodeError,
            ) as e:
                if attempt == self.max_retries:
                    raise EmbeddingError(f"{self.model}: {type(e).__name__}: {e}") from e
            time.sleep(2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def from_settings(settings: ModelsSettings, *, timeout: float, max_retries: int) -> Embedder | None:
    """`models.toml` 的 `[embedding]`；沒有設定檔或沒有這一段時為 None，檢索只用字面臂。"""
    if settings.ontology_models is None:
        return None
    from envcite_ontology.verification import config  # 延後匯入：verification 套件會回頭用到 store

    return config.load_embedder(settings.ontology_models, timeout=timeout, max_retries=max_retries)


def to_sql(vector: Sequence[float]) -> str:
    """pgvector 的文字表示，送進 `%s::vector`。"""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
