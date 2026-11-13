"""Transient failures of the model endpoint: retried with backoff; the rest stop at once.

A loopback server answers from a script, so what is proved is the retry policy and what the pipeline
does with a model that never answers. No provider is called."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.verification.config import parse
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.gates import GateResult
from envcite_ontology.verification.models import ChatCompletionsModel, ModelError
from envcite_ontology.verification.pipeline import verify
from tests.test_annotation import LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED, _two_statements

Conn = psycopg.Connection[tuple[object, ...]]
GOOD = {"choices": [{"message": {"content": "答"}}]}


class Scripted:
    """A chat-completions server that answers (status, headers, body) in turn; the last repeats."""

    def __init__(self, *script: tuple[int, dict[str, str], Any]) -> None:
        self.script = list(script)
        self.requests = 0
        scripted = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                status, headers, body = scripted.script[
                    min(scripted.requests, len(scripted.script) - 1)
                ]
                scripted.requests += 1
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def waits() -> list[float]:
    return []


def model(url: str, waits: list[float], **kw: Any) -> ChatCompletionsModel:
    return ChatCompletionsModel(
        "m", family="f", base_url=url, sleep=waits.append, backoff=1.0, **kw
    )


@pytest.fixture
def served(request: pytest.FixtureRequest) -> Iterator[Any]:
    servers: list[Scripted] = []
    yield servers
    for s in servers:
        s.server.shutdown()


def test_a_transient_failure_is_retried_until_it_succeeds(served: Any, waits: list[float]) -> None:
    s = Scripted((503, {}, {}), (502, {}, {}), (200, {}, GOOD))
    served.append(s)

    assert model(s.url, waits).complete("問") == "答"

    assert s.requests == 3 and len(waits) == 2
    assert waits[1] > waits[0] * 1.0  # 指數退避（含抖動）：第二次等得比第一次久


def test_it_gives_up_after_the_allowed_retries(served: Any, waits: list[float]) -> None:
    s = Scripted((503, {}, {}))
    served.append(s)

    with pytest.raises(ModelError, match="HTTP 503"):
        model(s.url, waits, max_retries=2).complete("問")

    assert s.requests == 3 and len(waits) == 2


def test_conflict_and_too_early_are_not_retried(served: Any, waits: list[float]) -> None:
    for status in (409, 425):
        s = Scripted((status, {}, {}))
        served.append(s)
        with pytest.raises(ModelError, match=f"HTTP {status}"):
            model(s.url, waits).complete("問")
        assert s.requests == 1
    assert waits == []


def test_a_cdn_status_is_retried(served: Any, waits: list[float]) -> None:
    s = Scripted((520, {}, {}), (200, {}, GOOD))
    served.append(s)

    assert model(s.url, waits).complete("問") == "答"
    assert s.requests == 2 and len(waits) == 1


def test_a_client_error_is_not_retried(served: Any, waits: list[float]) -> None:
    s = Scripted((401, {}, {"error": "bad key"}))
    served.append(s)

    with pytest.raises(ModelError, match="HTTP 401"):
        model(s.url, waits).complete("問")

    assert s.requests == 1 and waits == []


def test_a_retry_after_header_is_followed(served: Any, waits: list[float]) -> None:
    s = Scripted((429, {"Retry-After": "7"}, {}), (200, {}, GOOD))
    served.append(s)

    model(s.url, waits).complete("問")

    assert waits == [7.0]


def test_a_retry_after_header_is_capped_at_a_minute(served: Any, waits: list[float]) -> None:
    s = Scripted((429, {"Retry-After": "3600"}, {}), (200, {}, GOOD))
    served.append(s)

    model(s.url, waits).complete("問")

    assert waits == [60.0]


def test_a_reply_that_is_not_json_is_retried(served: Any, waits: list[float]) -> None:
    s = Scripted((200, {}, b"<html>gateway hiccup</html>"), (200, {}, GOOD))
    served.append(s)

    assert model(s.url, waits).complete("問") == "答"
    assert s.requests == 2


def test_a_refused_connection_is_retried_then_reported(waits: list[float]) -> None:
    with pytest.raises(ModelError, match="URLError"):
        model("http://127.0.0.1:9/v1", waits, max_retries=2).complete("問")

    assert len(waits) == 2


def test_zero_retries_means_one_attempt(served: Any, waits: list[float]) -> None:
    s = Scripted((503, {}, {}))
    served.append(s)

    with pytest.raises(ModelError):
        model(s.url, waits, max_retries=0).complete("問")

    assert s.requests == 1 and waits == []


def test_the_retry_count_can_be_set_in_the_config(served: Any) -> None:
    s = Scripted((503, {}, {}))
    served.append(s)
    text = f"""
[endpoint.e]
base_url = "{s.url}"

[model.a]
endpoint = "e"
name = "m"
family = "f"
max_retries = 0

[roles]
extractor = "a"
roundtrip = "a"
independent_test = "a"
panel = ["a"]
"""
    models = parse(text, {"ENVCITE_E_API_KEY": "k"})

    with pytest.raises(ModelError):
        models.extractor.complete("問")

    assert s.requests == 1


# ---- what the pipeline does with a model that does not answer ----


class Mute:
    name = "fake:mute"
    family = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        raise ModelError("fake:mute: HTTP 503")


def test_a_unit_whose_extraction_gets_no_answer_is_not_written_and_is_tried_again(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    mute = Mute()

    out = extract_unit(conn, mute, LAW, "art_1", agent_version="1")

    assert out.loaded is None and out.dropped[0].startswith("<model error:")
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0
    assert mute.calls == 1  # 不修復：沒有第一版可修


def test_a_repair_that_gets_no_answer_keeps_the_first_version(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("排放廢水者應符合標準", "乙一")))
    incomplete = json.loads(json.dumps(EXTRACTED))
    incomplete["statements"][0]["conditions"] = incomplete["statements"][0]["conditions"][:1]

    class OnceThenMute:
        name = "fake:once"
        family = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str) -> str:
            self.calls += 1
            if self.calls > 1:
                raise ModelError("gone")
            return json.dumps(incomplete, ensure_ascii=False)

    model_ = OnceThenMute()

    out = extract_unit(conn, model_, LAW, "art_1", agent_version="1")

    assert model_.calls == 2  # 第二次（修復）沒有答案，就用第一版
    assert out.loaded is None  # 第一版缺 duty，修復後仍缺 → 不寫入，而不是中斷


class Failing:
    method = "flaky"
    verifier = "gate:flaky@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        raise ModelError("gate model did not answer")


def test_a_gate_whose_model_does_not_answer_is_inconclusive_not_fatal(
    conn: Conn, tmp_path: Path
) -> None:
    loaded = _two_statements(conn, tmp_path)

    written = verify(conn, [Failing()])

    assert written == len(loaded.ids)
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.verification"
            " WHERE method = 'flaky' AND result = 'inconclusive'"
            " AND evidence->>'error' LIKE 'gate model%%'",
        )
        == written
    )
    assert (
        _scalar(conn, "SELECT count(*) FROM annotation.assertion WHERE confirm_state = 'rejected'")
        == 0
    )
