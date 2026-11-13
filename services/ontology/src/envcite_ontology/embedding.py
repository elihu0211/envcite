"""嵌入模型：OpenAI 相容的 `/embeddings` 端點，只用標準函式庫（同 `verification.models`）。

語意臂在兩處用它：投影時為段落補嵌入（批次，重試與斷路由 `envcite_jobkit` 的政策處置），
查詢時為問題取向量（逾時短、不重試，取不到就只用字面臂）。這裡每次只送一個請求，
失敗以例外型別表明處置：端點問題、這一批的內容問題、或設定錯誤。端點與模型宣告在
`models.toml` 的 `[embedding]`（`verification.config`），沒有預設值：不把法規文字或
客戶的問題悄悄送到未指定的第三方。
"""

import logging
from collections.abc import Mapping, Sequence

from envcite_jobkit import Disposition, EndpointError, FatalError, ItemError, classify_http_status
from envcite_ontology.http import HttpStatus, TransportError, post_json

log = logging.getLogger(__name__)

#: retrieval.passage.embedding 的維度；換維度不同的模型要另一支遷移。
DIMENSION = 1024


class EmbeddingError(RuntimeError):
    """端點沒有給出可用的向量。"""


class EndpointUnavailable(EmbeddingError, EndpointError):
    """端點的狀況：連不上、逾時、408／429／5xx（含 CDN 的 520–524）、回應不完整。

    稍後重試可能就好。
    """


class EmbeddingRejected(EmbeddingError, ItemError):
    """端點拒收這一批的內容（413 以外的 4xx）：重送同樣內容沒用，跳過這一批。"""


class PayloadTooLarge(EmbeddingRejected):
    """HTTP 413：端點的輸入驗證拒收（模型的輸入上限是逐段的 token 數，不是整批大小）。"""


class EndpointMisconfigured(EmbeddingError, FatalError):
    """401／403 或維度不符：設定錯了，每一批都會失敗。"""


class Embedder:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Embedder({self.model!r})"  # 不印金鑰

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """依序回傳每段文字的向量。

        413 是確定性的，重試無益：整批 413 就對半拆開各自重送；單段仍 413（超過模型輸入上限）
        才截短，並警告，不讓一段過長的文字使整個同步失敗。其他失敗照例外型別交給呼叫端。
        """
        if not texts:
            return []
        try:
            return self._request(texts)
        except PayloadTooLarge:
            if len(texts) > 1:
                mid = len(texts) // 2
                return [*self.embed(texts[:mid]), *self.embed(texts[mid:])]
            return [self._embed_truncated(texts[0])]

    def _embed_truncated(self, text: str) -> list[float]:
        """單段超過模型輸入上限：每次留 3/4 重送，直到端點收下。"""
        kept = len(text)
        while kept > 1:
            kept = kept * 3 // 4
            try:
                vector = self._request([text[:kept]])[0]
            except PayloadTooLarge:
                continue
            log.warning(
                "%s: 一段文字超過模型輸入上限，已自 %d 字截至 %d 字再嵌入（開頭：%r）",
                self.model,
                len(text),
                kept,
                text[:30],
            )
            return vector
        raise PayloadTooLarge(f"{self.model}: HTTP 413 even for a single character")

    def _request(self, texts: Sequence[str]) -> list[list[float]]:
        data = self._post({"model": self.model, "input": list(texts)}).get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EndpointUnavailable(f"{self.model}: expected {len(texts)} embeddings")
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0):
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != DIMENSION:
                raise EndpointMisconfigured(
                    f"{self.model}: an embedding is not {DIMENSION} numbers"
                )
            vectors.append([float(x) for x in vector])
        return vectors

    def _post(self, body: Mapping[str, object]) -> dict[str, object]:
        try:
            payload = post_json(
                f"{self._base_url}/embeddings", body, api_key=self._api_key, timeout=self.timeout
            )
        except HttpStatus as e:
            raise _http_error(self.model, e.status) from e
        except TransportError as e:
            raise EndpointUnavailable(f"{self.model}: {e}") from e
        if not isinstance(payload, dict):
            raise EndpointUnavailable(f"{self.model}: the reply is not a JSON object")
        return payload


def _http_error(model: str, status: int) -> EmbeddingError:
    """狀態碼依 `envcite_jobkit.classify_http_status` 分類。"""
    message = f"{model}: HTTP {status}"
    if status == 413:
        return PayloadTooLarge(message)
    match classify_http_status(status):
        case Disposition.ENDPOINT:
            return EndpointUnavailable(message)
        case Disposition.FATAL:
            return EndpointMisconfigured(message)
        case _:
            return EmbeddingRejected(message)


def to_sql(vector: Sequence[float]) -> str:
    """pgvector 的文字表示，送進 `%s::vector`。"""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
