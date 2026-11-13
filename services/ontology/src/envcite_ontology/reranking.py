"""精排模型：Cohere／LiteLLM 相容的 `/rerank` 端點，只用標準函式庫（同 `embedding`）。

檢索先由兩臂（與函釋）取較深的候選，再由交叉編碼器逐一對（查詢，條文）打分、重排；它補的是兩臂
的排序品質與召回深度，補不到兩臂都沒取到的條文。端點與模型宣告在 `models.toml` 的 `[reranker]`
（`verification.config`），沒有預設值：不把法規文字或客戶的問題悄悄送到未指定的第三方；沒有這一段
就不精排，檢索照舊。

這是**查詢路徑上的可選加強**，不是必經的一步：多一次對外呼叫（30 條約 1 至 2 秒），所以不重試、
逾時短，任何失敗都以 `RerankError` 表明，由呼叫端退回精排前的次序，查詢不因此失敗。
"""

import logging
import math
from collections.abc import Callable, Sequence
from typing import Protocol

from envcite_ontology.http import HttpStatus, TransportError, post_json

log = logging.getLogger(__name__)

#: 送給精排模型的單條上限（字）。模型能讀的長度遠大於此，但條文開頭就帶著主題，截短不傷判斷，
#: 又讓整批請求的大小與延遲有界。
MAX_DOCUMENT_CHARS = 2000


class RerankError(RuntimeError):
    """端點沒有給出可用的分數：連不上、逾時、非 2xx、回應的形狀不對。"""


class Reranks(Protocol):
    """精排的介面；伺服器與評估工具只依賴它，測試以替身實作。"""

    model: str

    def score(
        self, query: str, documents: Sequence[str], *, timeout: float | None = None
    ) -> list[float]: ...


class Reranker:
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Reranker({self.model!r})"  # 不印金鑰

    def score(
        self, query: str, documents: Sequence[str], *, timeout: float | None = None
    ) -> list[float]:
        """依輸入次序回傳每一條對查詢的相關機率（0 至 1）。

        `timeout`（秒）是呼叫端剩下的預算，與自己的上限取小者；沒有剩餘就不發請求。端點回的是依分數排序的
        `results`（每筆帶 `index` 指回輸入的位置），這裡依 `index` 還原次序，
        並要求每一條恰有一個有限的分數，缺一或重複都不收：半套的分數會讓次序悄悄偏掉，比退回精排前的次序更糟。
        """
        if not documents:
            return []  # 端點對空清單回 500
        budget = self.timeout if timeout is None else min(self.timeout, timeout)
        if budget <= 0:
            raise RerankError(f"{self.model}: no time left")
        body = {
            "model": self.model,
            "query": query,
            # 空字串端點未必收，換成一個空白
            "documents": [d[:MAX_DOCUMENT_CHARS] or " " for d in documents],
            "top_n": len(documents),
        }
        try:
            payload = post_json(
                f"{self._base_url}/rerank", body, api_key=self._api_key, timeout=budget
            )
        except HttpStatus as e:
            raise RerankError(f"{self.model}: HTTP {e.status}") from e
        except TransportError as e:
            raise RerankError(f"{self.model}: {e}") from e
        return self._scores(payload, len(documents))

    def _scores(self, payload: object, count: int) -> list[float]:
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or len(results) != count:
            raise RerankError(f"{self.model}: expected {count} results")
        scores: list[float | None] = [None] * count
        for item in results:
            index = item.get("index") if isinstance(item, dict) else None
            value = item.get("relevance_score") if isinstance(item, dict) else None
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < count
                or scores[index] is not None
                or not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise RerankError(f"{self.model}: a result is malformed or repeated")
            scores[index] = float(value)
        return [s for s in scores if s is not None]


def select[T](
    ranked: Sequence[T],
    scores: Sequence[float],
    *,
    limit: int,
    extra: int = 0,
    is_extra: Callable[[T], bool] = lambda _: False,
) -> list[tuple[T, float]]:
    """依分數由高到低取前 `limit` 條，同分依輸入的名次；回傳（項目，分數）。

    `is_extra` 認得的項目（函釋帶出的條文）在前 `limit` 條之外時，
    再依分數補最多 `extra` 條接在後面：
    精排只看（查詢，條文）一對，看不到「有函釋指向它」這個依據，不能因此把它擠出去（契約是函釋帶出的條文
    不擠掉兩臂的結果、也不被擠掉）。
    """
    if len(scores) != len(ranked):
        raise ValueError("one score per item")
    order = sorted(range(len(ranked)), key=lambda i: (-scores[i], i))
    kept = order[:limit]
    kept += [i for i in order[limit:] if is_extra(ranked[i])][:extra]
    return [(ranked[i], scores[i]) for i in kept]
