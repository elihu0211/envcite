"""司法院 API 的真正 POST：走共用的對外 HTTP 層，自報身分，失敗不洩漏請求內容。"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from canon.law.judicial_api import EndpointError, http_post
from canon.net import USER_AGENT


class Endpoint:
    """記住收到什麼的單一路由伺服器。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.reply = b'{"Token": "t"}'
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", 0))
                endpoint.requests.append(
                    {
                        "user_agent": self.headers.get("User-Agent"),
                        "body": json.loads(self.rfile.read(size)),
                    }
                )
                self.send_response(endpoint.status)
                self.send_header("Content-Length", str(len(endpoint.reply)))
                self.end_headers()
                self.wfile.write(endpoint.reply)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/jdg/api/Auth"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    e = Endpoint()
    yield e
    e.server.shutdown()


def test_post_sends_json_with_the_shared_user_agent(endpoint: Endpoint) -> None:
    assert http_post(endpoint.url, {"user": "u", "password": "p"}) == {"Token": "t"}

    (sent,) = endpoint.requests
    assert sent["user_agent"] == USER_AGENT
    assert sent["body"] == {"user": "u", "password": "p"}


@pytest.mark.parametrize(
    ("status", "reply", "expected"),
    [(500, b"{}", "Auth HTTP 500"), (200, b"not json", "Auth JSONDecodeError")],
)
def test_failures_become_endpoint_errors_that_name_the_kind_but_not_the_request(
    endpoint: Endpoint, status: int, reply: bytes, expected: str
) -> None:
    endpoint.status, endpoint.reply = status, reply

    with pytest.raises(EndpointError) as error:
        http_post(endpoint.url, {"user": "u", "password": "secret-password"})

    assert str(error.value) == expected
    assert "secret-password" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
