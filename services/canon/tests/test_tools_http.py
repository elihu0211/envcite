"""評估工具的對外呼叫：走共用的對外 HTTP 層，自報身分，錯誤與重試的行為不變。"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

import query_routing
from canon.net import USER_AGENT
from grounding_check_eval import Chat


class Endpoint:
    """依序回覆預先排好的 (狀態, 本文)，用完後重複最後一個；記住收到什麼。"""

    def __init__(self, *replies: tuple[int, object]) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", 0))
                endpoint.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "user_agent": self.headers.get("User-Agent"),
                        "body": json.loads(self.rfile.read(size)),
                    }
                )
                index = min(len(endpoint.requests), len(endpoint.replies)) - 1
                status, body = endpoint.replies[index]
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def served() -> Iterator[list[Endpoint]]:
    """測試要的伺服器都登記在這裡，結束時一起關。"""
    made: list[Endpoint] = []
    yield made
    for e in made:
        e.server.shutdown()


def _serve(served: list[Endpoint], *replies: tuple[int, object]) -> Endpoint:
    e = Endpoint(*replies)
    served.append(e)
    return e


def test_a_live_query_posts_the_key_and_identifies_itself(served: list[Endpoint]) -> None:
    e = _serve(served, (200, [{"a": "x"}]))

    assert query_routing.fetch_live(e.url + "/", "k", "噪音") == [{"a": "x"}]

    (sent,) = e.requests
    assert sent["path"] == "/v2/search"
    assert sent["auth"] == "Bearer k"
    assert sent["user_agent"] == USER_AGENT
    assert sent["body"] == {"query": "噪音", "asOf": None, "limit": None}


def test_a_live_error_status_is_explained_and_stops(served: list[Endpoint]) -> None:
    e = _serve(served, (401, {}))

    with pytest.raises(SystemExit) as stop:
        query_routing.fetch_live(e.url, "k", "噪音")

    assert stop.value.code == query_routing.explain_http_error(401)


def _chat(tmp_path: Path, url: str, monkeypatch: pytest.MonkeyPatch, max_calls: int = 10) -> Chat:
    models = tmp_path / "models.toml"
    models.write_text(
        f'[endpoint.llm]\nbase_url = "{url}/v1"\n[model.judge]\nendpoint = "llm"\nname = "m"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ENVCITE_LLM_API_KEY", "k")
    return Chat(models, "judge", max_calls)


REPLY = {
    "choices": [{"message": {"content": "答"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
}


def test_a_completion_is_posted_with_the_key_and_counts_tokens(
    served: list[Endpoint], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = _serve(served, (200, REPLY))
    chat = _chat(tmp_path, e.url, monkeypatch)

    assert chat.complete("系統", "問題") == ("答", 1, None)

    (sent,) = e.requests
    assert sent["path"] == "/v1/chat/completions"
    assert sent["auth"] == "Bearer k"
    assert sent["user_agent"] == USER_AGENT
    assert sent["body"]["messages"][1] == {"role": "user", "content": "問題"}
    assert dict(chat.tokens) == {"input": 3, "output": 2}


def test_a_server_error_is_retried_and_a_client_error_is_not(
    served: list[Endpoint], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = _serve(served, (503, {}), (200, REPLY))
    assert _chat(tmp_path, flaky.url, monkeypatch).complete("s", "u") == ("答", 2, None)

    refused = _serve(served, (400, {}))
    assert _chat(tmp_path, refused.url, monkeypatch).complete("s", "u") == (None, 1, "http 400")
    assert len(refused.requests) == 1


def test_an_unreadable_reply_is_reported_not_raised(
    served: list[Endpoint], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e = _serve(served, (200, {"id": "no choices"}))

    text, used, last = _chat(tmp_path, e.url, monkeypatch).complete("s", "u", retries=0)

    assert (text, used, last) == (None, 1, "KeyError")
