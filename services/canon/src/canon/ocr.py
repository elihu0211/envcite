"""OCR：以視覺語言模型的 Chat Completions 端點，把一頁圖片逐字讀成文字。

端點與模型宣告在 `models.toml` 的 `[ocr]`（`verification.config`），沒有預設值：不把裁判書悄悄送到
未指定的第三方；沒有這一段就不做 OCR，掃描檔只記 `file_only`。選型依據是在端點上以繁體中文圖片量
字元錯誤率（見 PR 說明），不是任何公開榜單。

任何失敗以 `OcrError` 表明，不重試，由呼叫端決定留待下一夜或放棄。
"""

import base64
from collections.abc import Callable
from typing import Protocol

from canon.net import HttpStatus, TransportError, post_json

#: 一頁輸出的詞元上限；裁判書一頁遠小於此，留餘裕給密集的頁面。
MAX_TOKENS = 4096
PROMPT = "請逐字辨識圖片中的全部文字，原樣輸出，不要翻譯、不要加說明或標點以外的符號。"

type Post = Callable[..., object]


#: 回應的 `finish_reason` 是這些時，文字不完整（截斷、重複迴圈、被過濾）；沒有這個欄位或是
#: `stop` 才當成讀完。
_INCOMPLETE = frozenset({"length", "repetition", "content_filter"})


class OcrError(RuntimeError):
    """端點沒有給出可用的文字：連不上、逾時、非 2xx、回應的形狀不對。"""


class OcrIncomplete(OcrError):
    """端點回了文字，但沒有正常結束（截斷、重複迴圈）。

    與連線失敗不同：溫度 0 下同一頁會再次出現，重試不會改變結果，所以呼叫端不該留待下一夜。
    """


class Reads(Protocol):
    """OCR 的介面；取得流程只依賴它，測試以替身實作。"""

    model: str

    def read(self, image: bytes, *, timeout: float | None = None) -> str:
        """一頁 JPEG 圖片的文字；任何失敗以 `OcrError` 表明。"""
        ...


class Ocr:
    """`Reads` 的實作：呼叫 OpenAI 相容的 `/chat/completions`，圖片以 data URI 帶入，溫度 0。"""

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 180.0,
        post: Post = post_json,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self._post = post

    def __repr__(self) -> str:
        return f"Ocr({self.model!r})"  # 不印金鑰

    def read(self, image: bytes, *, timeout: float | None = None) -> str:
        """一頁 JPEG 的文字（去頭尾空白）。"""
        data = base64.b64encode(image).decode("ascii")
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{data}"},
                        },
                    ],
                }
            ],
        }
        budget = self.timeout if timeout is None else min(self.timeout, timeout)
        try:
            payload = self._post(
                f"{self._base_url}/chat/completions", body, api_key=self._api_key, timeout=budget
            )
        except HttpStatus as e:
            raise OcrError(f"{self.model}: HTTP {e.status}") from e
        except TransportError as e:
            raise OcrError(f"{self.model}: {e}") from e
        return self._text(payload)

    def _text(self, payload: object) -> str:
        choices = payload.get("choices") if isinstance(payload, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OcrError(f"{self.model}: no text in the reply")
        reason = first.get("finish_reason") if isinstance(first, dict) else None
        if reason in _INCOMPLETE:
            raise OcrIncomplete(f"{self.model}: the reply stopped at {reason}")
        return content.strip()
