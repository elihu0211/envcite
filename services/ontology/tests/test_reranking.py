"""精排：端點用戶端（本機的 HTTP 伺服器）與取前 N 條的純函式。

這裡證明的是請求的格式、回應的還原與失敗的處置；端點實際怎麼打分不在這裡證明，沒有呼叫任何外部服務。
"""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from envcite_ontology.http import USER_AGENT
from envcite_ontology.reranking import MAX_DOCUMENT_CHARS, Reranker, RerankError, select


class Endpoint:
    """一條 `/rerank` 路由的伺服器，記下收到的請求；分數依文件裡的標記字決定（越多「好」越高）。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.mode = "ok"
        self.delay = 0.0
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(size))
                endpoint.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "user_agent": self.headers.get("User-Agent"),
                        "body": body,
                    }
                )
                time.sleep(endpoint.delay)
                data = json.dumps(endpoint.reply(body["documents"])).encode()
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

    def reply(self, documents: list[str]) -> dict[str, Any]:
        scored: list[dict[str, Any]] = [
            {"index": i, "relevance_score": d.count("好") / 10} for i, d in enumerate(documents)
        ]
        # 真實端點依分數由高到低回，而不是依輸入的次序
        results = sorted(scored, key=lambda r: -r["relevance_score"])
        match self.mode:
            case "missing":
                results = results[:-1]
            case "repeated":
                results[-1] = dict(results[0])
            case "out_of_range":
                results[0] = {**results[0], "index": len(documents)}
            case "not_a_number":
                results[0] = {**results[0], "relevance_score": "high"}
            case "boolean":
                results[0] = {**results[0], "relevance_score": True}
            case "no_results":
                return {"error": "nope"}
        return {"id": "x", "results": results}


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    e = Endpoint()
    yield e
    e.server.shutdown()


def reranker(e: Endpoint, **kw: Any) -> Reranker:
    return Reranker("bge-m3-reranker", base_url=e.url, api_key="k", **kw)


def test_scores_come_back_in_the_order_the_documents_were_sent(endpoint: Endpoint) -> None:
    scores = reranker(endpoint).score("露天燃燒", ["不相干", "好好好", "好"])

    assert scores == [0.0, 0.3, 0.1]  # 端點回的是依分數排序的，次序由 index 還原
    (sent,) = endpoint.requests
    assert sent["path"] == "/v1/rerank"
    assert sent["auth"] == "Bearer k"
    assert sent["user_agent"] == USER_AGENT
    assert sent["body"] == {
        "model": "bge-m3-reranker",
        "query": "露天燃燒",
        "documents": ["不相干", "好好好", "好"],
        "top_n": 3,
    }


def test_a_long_document_is_cut_and_an_empty_one_is_not_sent_empty(endpoint: Endpoint) -> None:
    reranker(endpoint).score("問", ["好" * (MAX_DOCUMENT_CHARS + 500), ""])

    (sent,) = endpoint.requests
    assert sent["body"]["documents"] == ["好" * MAX_DOCUMENT_CHARS, " "]


def test_nothing_to_rerank_makes_no_request(endpoint: Endpoint) -> None:
    assert reranker(endpoint).score("問", []) == []
    assert endpoint.requests == []


def test_an_http_error_is_a_rerank_error_that_does_not_carry_the_key(endpoint: Endpoint) -> None:
    endpoint.status = 500

    keyed = Reranker("bge-m3-reranker", base_url=endpoint.url, api_key="distinct-key-123")
    with pytest.raises(RerankError, match="HTTP 500") as raised:
        keyed.score("問", ["好"])

    assert "distinct-key-123" not in str(raised.value)


@pytest.mark.parametrize(
    "mode", ["missing", "repeated", "out_of_range", "not_a_number", "boolean", "no_results"]
)
def test_a_partial_or_malformed_reply_is_rejected_whole(endpoint: Endpoint, mode: str) -> None:
    """半套的分數會讓次序悄悄偏掉，比不精排更糟，所以一個都不收。"""
    endpoint.mode = mode

    with pytest.raises(RerankError):
        reranker(endpoint).score("問", ["好", "好好", "好好好"])


def test_a_slow_endpoint_times_out_instead_of_holding_the_query(endpoint: Endpoint) -> None:
    endpoint.delay = 1.0

    started = time.monotonic()
    with pytest.raises(RerankError):
        reranker(endpoint, timeout=0.1).score("問", ["好"])

    assert time.monotonic() - started < 0.9


def test_the_callers_remaining_budget_caps_the_timeout(endpoint: Endpoint) -> None:
    endpoint.delay = 1.0

    started = time.monotonic()
    with pytest.raises(RerankError):
        reranker(endpoint, timeout=30.0).score("問", ["好"], timeout=0.1)

    assert time.monotonic() - started < 0.9


def test_no_time_left_means_no_request(endpoint: Endpoint) -> None:
    with pytest.raises(RerankError, match="no time left"):
        reranker(endpoint).score("問", ["好"], timeout=0.0)

    assert endpoint.requests == []


def test_an_unreachable_endpoint_is_a_rerank_error() -> None:
    gone = Reranker("m", base_url="http://127.0.0.1:1/v1", timeout=1.0)

    with pytest.raises(RerankError):
        gone.score("問", ["好"])


def test_the_repr_does_not_print_the_key(endpoint: Endpoint) -> None:
    assert "secret-value" not in repr(Reranker("m", base_url=endpoint.url, api_key="secret-value"))


def test_select_orders_by_score_and_breaks_ties_by_the_earlier_rank() -> None:
    kept = select(["a", "b", "c", "d"], [0.2, 0.9, 0.9, 0.1], limit=3)

    assert kept == [("b", 0.9), ("c", 0.9), ("a", 0.2)]


def test_select_keeps_guided_items_pushed_out_of_the_top_up_to_the_extra_allowance() -> None:
    """函釋指向的條文不能因為精排只看（查詢，條文）一對就被擠掉。"""
    items = ["x", "g1", "y", "g2", "g3"]
    kept = select(
        items, [0.9, 0.1, 0.8, 0.3, 0.2], limit=2, extra=2, is_extra=lambda i: i.startswith("g")
    )

    assert [i for i, _ in kept] == ["x", "y", "g2", "g3"]  # g1 分數最低，超過名額就不留


def test_select_does_not_count_a_guided_item_twice() -> None:
    kept = select(["g1", "y"], [0.9, 0.1], limit=1, extra=5, is_extra=lambda i: i.startswith("g"))

    assert [i for i, _ in kept] == ["g1"]  # 已在前 limit 條，不重複補


def test_select_needs_one_score_per_item() -> None:
    with pytest.raises(ValueError, match="one score per item"):
        select(["a", "b"], [0.5], limit=1)


def test_select_of_nothing_is_nothing() -> None:
    assert select([], [], limit=3) == []
