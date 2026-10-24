"""The OpenAI-compatible adapter against a real HTTP server on loopback.

What this proves is the wire format and the failure handling. What it cannot prove is that any
particular provider behaves as documented; no provider is called here."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from envcite_ontology.verification import ChatCompletionsModel


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
def endpoint() -> Iterator[Endpoint]:
    e = Endpoint()
    yield e
    e.server.shutdown()


def chat(e: Endpoint, **kw: Any) -> ChatCompletionsModel:
    kw.setdefault("api_key", "k")
    return ChatCompletionsModel("openai/gpt-oss-120b", family="openai", base_url=e.url, **kw)


def test_one_user_message_goes_to_the_chat_completions_route(endpoint: Endpoint) -> None:
    text = chat(endpoint, max_tokens=100).complete("問題")

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

    assert chat(endpoint).complete("x") == '{"faithful": true}'


def test_a_local_server_without_a_key_gets_no_authorization_header(endpoint: Endpoint) -> None:
    chat(endpoint, api_key=None).complete("x")

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
        chat(endpoint).complete("x")


def test_an_http_error_names_the_status_and_not_the_key(endpoint: Endpoint) -> None:
    endpoint.status = 429
    endpoint.reply = {"error": "rate limited"}

    with pytest.raises(RuntimeError) as raised:
        chat(endpoint).complete("x")

    assert "429" in str(raised.value) and "Bearer" not in str(raised.value)


def test_the_name_is_family_and_model_id_and_the_model_id_goes_out_verbatim(
    endpoint: Endpoint,
) -> None:
    model = chat(endpoint)
    model.complete("x")

    assert (model.name, model.family) == ("openai:openai/gpt-oss-120b", "openai")
    assert endpoint.requests[0]["body"]["model"] == "openai/gpt-oss-120b"


def test_no_response_format_is_sent_unless_asked(endpoint: Endpoint) -> None:
    chat(endpoint).complete("x")

    assert "response_format" not in endpoint.requests[0]["body"]


def test_a_response_format_goes_into_the_request_as_given(endpoint: Endpoint) -> None:
    fmt = {"type": "json_schema", "json_schema": {"name": "a", "schema": {"type": "object"}}}

    chat(endpoint).complete("x", response_format=fmt)

    assert endpoint.requests[0]["body"]["response_format"] == fmt


def test_token_usage_accumulates_when_the_endpoint_reports_it(endpoint: Endpoint) -> None:
    endpoint.reply = {
        "choices": [{"message": {"content": "答"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    model = chat(endpoint)

    model.complete("x")
    model.complete("y")

    assert model.tokens == {"prompt": 20, "completion": 6}


def test_usage_stays_zero_when_the_endpoint_does_not_report_it(endpoint: Endpoint) -> None:
    model = chat(endpoint)

    model.complete("x")

    assert model.tokens == {"prompt": 0, "completion": 0}
