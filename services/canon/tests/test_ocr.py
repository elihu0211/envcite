"""OCR 用戶端：請求帶圖片與溫度 0，回應只取文字，任何失敗以 `OcrError` 表明。"""

import base64

import pytest

from canon.net import HttpStatus, TransportError
from canon.ocr import PROMPT, Ocr, OcrError, OcrIncomplete


class _Post:
    def __init__(self, reply: object = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, dict[str, object], str | None, float]] = []

    def __call__(
        self, url: str, body: dict[str, object], *, api_key: str | None, timeout: float
    ) -> object:
        self.calls.append((url, body, api_key, timeout))
        if self.error:
            raise self.error
        return self.reply


def _ocr(post: _Post) -> Ocr:
    return Ocr("vl", base_url="https://models.example/v1/", api_key="k", timeout=60.0, post=post)


def test_the_request_carries_the_image_and_a_zero_temperature() -> None:
    post = _Post({"choices": [{"message": {"content": " 本件適用噪音管制法。\n"}}]})

    text = _ocr(post).read(b"\xff\xd8jpeg", timeout=5.0)

    assert text == "本件適用噪音管制法。"
    url, body, key, timeout = post.calls[0]
    assert (url, key, timeout) == ("https://models.example/v1/chat/completions", "k", 5.0)
    assert body["model"] == "vl" and body["temperature"] == 0
    content = body["messages"][0]["content"]  # type: ignore[index]
    assert content[0] == {"type": "text", "text": PROMPT}
    assert (
        content[1]["image_url"]["url"]
        == "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg").decode()
    )


@pytest.mark.parametrize("reply", [None, {}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_a_reply_without_text_is_an_error(reply: object) -> None:
    with pytest.raises(OcrError):
        _ocr(_Post(reply)).read(b"x")


@pytest.mark.parametrize("reason", ["length", "repetition", "content_filter"])
def test_a_reply_that_did_not_end_normally_is_incomplete(reason: str) -> None:
    reply = {"choices": [{"message": {"content": "本件適用噪音管"}, "finish_reason": reason}]}

    with pytest.raises(OcrIncomplete, match=reason):
        _ocr(_Post(reply)).read(b"x")


@pytest.mark.parametrize("reason", ["stop", None])
def test_a_reply_that_ended_normally_or_does_not_say_is_text(reason: str | None) -> None:
    choice: dict[str, object] = {"message": {"content": "本件適用噪音管制法。"}}
    if reason is not None:
        choice["finish_reason"] = reason

    assert _ocr(_Post({"choices": [choice]})).read(b"x") == "本件適用噪音管制法。"


def test_an_incomplete_reply_is_also_an_ocr_error() -> None:
    assert issubclass(OcrIncomplete, OcrError)


@pytest.mark.parametrize("error", [HttpStatus(500, b"", {}), TransportError("timeout")])
def test_transport_and_status_failures_become_ocr_errors(error: Exception) -> None:
    with pytest.raises(OcrError):
        _ocr(_Post(error=error)).read(b"x")
