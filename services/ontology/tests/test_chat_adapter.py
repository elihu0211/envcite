"""The OpenAI-compatible adapter against a real HTTP server on loopback.

What this proves is the wire format and the failure handling. What it cannot prove is that any
particular provider behaves as documented; no provider is called here."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from env_law_ontology.verification import ChatCompletionsModel, build_model


class Endpoint:
    """A one-route chat-completions server that remembers what it was sent."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.reply: dict[str, Any] = {"choices": [{"message": {"content": "答"}}]}
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", 0))
                endpoint.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(size)),
                    }
                )
                data = json.dumps(endpoint.reply).encode()
                self.send_response(endpoint.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[Endpoint]:
    e = Endpoint()
    monkeypatch.setenv("CHAT_BASE_URL", e.url)
    monkeypatch.setenv("CHAT_API_KEY", "k")
    yield e
    e.server.shutdown()


def test_the_slug_names_the_model_and_the_vendor_is_its_family() -> None:
    model = ChatCompletionsModel("openai/gpt-oss-120b")

    assert (model.name, model.family) == ("openai:gpt-oss-120b", "openai")
    assert ChatCompletionsModel("qwen/qwen3-235b-a22b").family == "qwen"
    assert (
        ChatCompletionsModel("gpt-oss:120b").family == "unknown"
    )  # 沒有供應商前綴：保守地算同一家族


def test_one_user_message_goes_to_the_chat_completions_route(endpoint: Endpoint) -> None:
    text = ChatCompletionsModel("openai/gpt-oss-120b", max_tokens=100).complete("問題")

    assert text == "答"
    (sent,) = endpoint.requests
    assert sent["path"] == "/v1/chat/completions"
    assert sent["auth"] == "Bearer k"
    assert sent["body"] == {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": "問題"}],
        "max_tokens": 100,
        "temperature": 0,
    }


def test_a_reasoning_models_thinking_is_not_taken_for_the_answer(endpoint: Endpoint) -> None:
    endpoint.reply = {
        "choices": [{"message": {"content": '{"faithful": true}', "reasoning": "先想一想"}}]
    }

    assert ChatCompletionsModel("openai/gpt-oss-120b").complete("x") == '{"faithful": true}'


def test_a_local_server_without_a_key_gets_no_authorization_header(
    endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHAT_API_KEY")

    ChatCompletionsModel("openai/gpt-oss-120b").complete("x")

    assert endpoint.requests[0]["auth"] is None


@pytest.mark.parametrize(
    "reply",
    [
        {"choices": [{"message": {"content": None, "reasoning": "想到用完長度"}}]},
        {"choices": []},
        {},
    ],
)
def test_an_answer_without_content_is_an_error_not_an_empty_pass(
    endpoint: Endpoint, reply: dict[str, Any]
) -> None:
    endpoint.reply = reply

    with pytest.raises(RuntimeError, match="empty content"):
        ChatCompletionsModel("openai/gpt-oss-120b").complete("x")


def test_an_http_error_names_the_status_and_not_the_key(endpoint: Endpoint) -> None:
    endpoint.status = 429
    endpoint.reply = {"error": "rate limited"}

    with pytest.raises(RuntimeError) as raised:
        ChatCompletionsModel("openai/gpt-oss-120b").complete("x")

    assert "429" in str(raised.value) and "Bearer" not in str(raised.value)


def test_the_endpoint_must_be_named_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="CHAT_BASE_URL"):
        ChatCompletionsModel("openai/gpt-oss-120b").complete("x")


def test_build_model_accepts_the_chat_spec() -> None:
    model = build_model("chat:openai/gpt-oss-120b")

    assert isinstance(model, ChatCompletionsModel)
    assert model.name == "openai:gpt-oss-120b"
    with pytest.raises(ValueError):
        build_model("chat:")
