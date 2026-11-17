"""`canon.net` 的對外身分：User-Agent 取自套件中繼資料，不在程式裡寫死。"""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from canon.net import USER_AGENT, post_json


def test_user_agent_names_version_and_contact() -> None:
    """版本與聯絡處缺任一個都會讓取得站台無從得知找誰；缺 `[project.urls]` 時這裡會紅。"""
    assert re.fullmatch(r"canon/\d+\.\d+\.\d+ \(\+https://\S+\)", USER_AGENT)


def test_consecutive_posts_to_one_endpoint_reuse_the_connection() -> None:
    """每次都重新握手，嵌入與精排的尾端延遲就多一次 TCP／TLS；連續請求應走同一條連線。"""
    peers: set[int] = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            peers.add(self.client_address[1])
            data = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        for _ in range(3):
            assert post_json(url, {}, api_key=None, timeout=5.0) == {}
    finally:
        server.shutdown()

    assert len(peers) == 1
