"""問句的案件事實：抽取函式庫與 `ExtractFacts` 端點。

模型與嵌入端點是替身，其餘（詞彙來自已確認的陳述、程式檢查、錯誤模型）是真的。
"""

import json
from pathlib import Path

import grpc
import pytest

from canon.annotate import load
from canon.case_facts import CaseFactExtractor, Extraction, Unavailable
from canon.db import Conn
from canon.reasoning_service import MAX_QUESTION_CHARS, ReasoningServicer
from canon.store.canon_store import CanonStore
from canon.verification.models import ModelError
from envcite.canon.v1 import reasoning_pb2 as pb
from tests.grpc_support import Aborted, FakeContext
from tests.test_annotation import BEFORE, _doc, _duty, _limit
from tests.test_incremental import _first, _law_v1

QUESTION = "我們有排放廢水，化學需氧量是150"


class Replies:
    """依序回答的模型替身，記下收到的提示。"""

    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


class NameVectors:
    """嵌入替身：名稱含「廢水」的向量指向 x，含「需氧」的指向 y，問句兩者都有，指向 x。"""

    model = "fake"

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return [
            [1.0, 0.0] if "廢水" in t else [0.0, 1.0] if "需氧" in t else [1.0, 0.1] for t in texts
        ]


def _answer(*concepts: dict[str, object]) -> dict[str, object]:
    return {"concepts": list(concepts)}


GOOD = _answer(
    {"concept": "discharging", "status": "有", "value": None, "quote": "我們有排放廢水"},
    {"concept": "cod", "status": "有", "value": 150, "quote": "化學需氧量是150"},
)


def _committed(conn: Conn, tmp_path: Path) -> None:
    """已確認的陳述：排放廢水（旗標）與化學需氧量的限值 100（數值），提交給伺服器看得到。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))
    conn.commit()


def _connect(store: CanonStore):  # type: ignore[no-untyped-def]
    return lambda: store.connections.read_connection()


def test_the_facts_the_question_states_come_back_checked(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)
    extractor = CaseFactExtractor(Replies(GOOD), None)

    out = extractor.extract(_connect(store), QUESTION, BEFORE)

    assert {(f.concept, f.value) for f in out.facts} == {("discharging", True), ("cod", 150.0)}
    assert out.names == {"discharging": "排放廢水", "cod": "化學需氧量"}
    assert out.candidates == 2


def test_a_fact_whose_quote_is_not_in_the_question_is_dropped(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)
    invented = _answer(
        {"concept": "discharging", "status": "有", "value": None, "quote": "問句沒有這句話"}
    )

    out = CaseFactExtractor(Replies(invented), None).extract(_connect(store), QUESTION, BEFORE)

    assert out.facts == []


def test_a_concept_outside_the_candidates_is_dropped(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)
    made_up = _answer(
        {"concept": "invented_concept", "status": "有", "value": None, "quote": "我們有排放廢水"}
    )

    out = CaseFactExtractor(Replies(made_up), None).extract(_connect(store), QUESTION, BEFORE)

    assert out.facts == []


def test_nothing_confirmed_means_no_facts_and_no_model_call(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _first(conn, tmp_path, _law_v1())  # 有法規、沒有陳述
    conn.commit()
    model = Replies(GOOD)

    out = CaseFactExtractor(model, None).extract(_connect(store), QUESTION, BEFORE)

    assert out == Extraction([], {}, 0)
    assert model.prompts == []


def test_a_large_vocabulary_offers_only_the_nearest_concepts(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)
    model = Replies(GOOD)

    out = CaseFactExtractor(model, NameVectors(), candidates=1).extract(
        _connect(store), "有排放廢水嗎", BEFORE
    )

    assert out.candidates == 1
    assert "- discharging｜排放廢水" in model.prompts[0]
    assert "化學需氧量｜" not in model.prompts[0]


def test_a_large_vocabulary_needs_an_embedder(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)

    with pytest.raises(Unavailable):
        CaseFactExtractor(Replies(GOOD), None, candidates=1).extract(
            _connect(store), QUESTION, BEFORE
        )


def test_the_vocabulary_is_read_once_per_interval(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)
    now = [0.0]
    extractor = CaseFactExtractor(Replies(GOOD), None, ttl=60.0, clock=lambda: now[0])
    reads = []

    def connect():  # type: ignore[no-untyped-def]
        reads.append(1)
        return store.connections.read_connection()

    extractor.extract(connect, QUESTION, BEFORE)
    extractor.extract(connect, QUESTION, BEFORE)
    now[0] = 61.0
    extractor.extract(connect, QUESTION, BEFORE)

    assert len(reads) == 2


# ---- 端點 ------------------------------------------------------------------------


def _servicer(store: CanonStore, model: object | None, **over: object) -> ReasoningServicer:
    extractor = None if model is None else CaseFactExtractor(model, None)  # type: ignore[arg-type]
    return ReasoningServicer(
        store.connections,
        store.laws,
        extractor=extractor,
        today=lambda: BEFORE,
        **over,  # type: ignore[arg-type]
    )


def test_the_endpoint_returns_typed_facts_ready_for_evaluate(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)

    response = _servicer(store, Replies(GOOD)).ExtractFacts(
        pb.ExtractFactsRequest(question=QUESTION), FakeContext()
    )

    by_concept = {f.concept: f for f in response.facts}
    assert by_concept["discharging"].value.flag is True
    assert by_concept["cod"].value.number.value == "150"
    assert by_concept["cod"].quote == "化學需氧量是150"
    assert by_concept["cod"].name == "化學需氧量"
    assert response.candidates == 2


def test_without_a_configured_model_the_endpoint_says_so(store: CanonStore) -> None:
    with pytest.raises(Aborted) as aborted:
        _servicer(store, None).ExtractFacts(pb.ExtractFactsRequest(question="問"), FakeContext())

    assert aborted.value.status.code == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.parametrize("question", ["", "   ", "字" * (MAX_QUESTION_CHARS + 1)])
def test_an_empty_or_overlong_question_is_refused(store: CanonStore, question: str) -> None:
    with pytest.raises(Aborted) as aborted:
        _servicer(store, Replies(GOOD)).ExtractFacts(
            pb.ExtractFactsRequest(question=question), FakeContext()
        )

    assert aborted.value.status.code == grpc.StatusCode.INVALID_ARGUMENT


def test_a_failing_model_is_unavailable_not_a_crash(
    conn: Conn, tmp_path: Path, store: CanonStore
) -> None:
    _committed(conn, tmp_path)

    with pytest.raises(Aborted) as aborted:
        _servicer(store, Replies(ModelError("端點逾時"))).ExtractFacts(
            pb.ExtractFactsRequest(question=QUESTION), FakeContext()
        )

    assert aborted.value.status.code == grpc.StatusCode.UNAVAILABLE


def test_a_call_that_cannot_get_a_slot_in_its_budget_fails_fast(store: CanonStore) -> None:
    servicer = _servicer(store, Replies(GOOD), extract_slots=1)
    assert servicer._extract_slots.acquire()  # 名額被占著

    with pytest.raises(Aborted) as aborted:
        servicer.ExtractFacts(pb.ExtractFactsRequest(question=QUESTION), FakeContext(0.05))

    assert aborted.value.status.code == grpc.StatusCode.RESOURCE_EXHAUSTED


def test_the_question_never_reaches_the_log(
    conn: Conn, tmp_path: Path, store: CanonStore, caplog: pytest.LogCaptureFixture
) -> None:
    _committed(conn, tmp_path)
    caplog.set_level("DEBUG")

    _servicer(store, Replies(GOOD)).ExtractFacts(
        pb.ExtractFactsRequest(question=QUESTION), FakeContext()
    )

    assert "排放廢水" not in caplog.text and "150" not in caplog.text
    assert "chars=" in caplog.text
