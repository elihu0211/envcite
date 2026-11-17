"""請求路徑的讀取預算：期限取消查詢、池子耗盡回 BUSY、嵌入名額滿了仍走字面臂。"""

import grpc
import psycopg
import pytest
from google.rpc import error_details_pb2, status_pb2
from psycopg_pool import ConnectionPool

from canon import errors, projection
from canon.embedding import EmbeddingError
from canon.search_tuning import SearchTuning
from canon.server import QUERY_EMBED_SLOTS, WORKERS
from canon.service import build_servicer
from canon.store.canon_store import CanonStore
from canon.store.connections import Connections
from envcite.canon.v1 import canon_pb2
from envcite_segmenter import Segmenter


class _Aborted(Exception):
    def __init__(self, status: grpc.Status) -> None:
        self.status = status


class _Context:
    def __init__(self, remaining: float | None) -> None:
        self._remaining = remaining

    def invocation_metadata(self) -> tuple[()]:
        return ()

    def time_remaining(self) -> float | None:
        return self._remaining

    def abort_with_status(self, status: grpc.Status) -> None:
        raise _Aborted(status)


class _CountingEmbedder:
    model = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise EmbeddingError("down")


def _reason(status: grpc.Status) -> str:
    raw = dict(status.trailing_metadata)["grpc-status-details-bin"]
    parsed = status_pb2.Status.FromString(raw)
    info = error_details_pb2.ErrorInfo()
    assert parsed.details[0].Unpack(info)
    return str(info.reason)


def test_embed_slots_stay_below_the_worker_pool() -> None:
    assert QUERY_EMBED_SLOTS < WORKERS


def test_a_spent_deadline_does_not_take_a_connection(
    store: CanonStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("checked out a connection after the deadline")

    monkeypatch.setattr(store.connections._pool, "connection", fail)
    with pytest.raises(psycopg.errors.QueryCanceled):
        store.retrieval.search("噪音", tokenizer="simple", timeout=0)


def test_a_short_deadline_cancels_the_search_query(
    store: CanonStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("canon.store.retrieval._SEARCH", "SELECT pg_sleep(5)")
    servicer = build_servicer(
        store.laws, store.graph, store.retrieval, Segmenter([]), store.retrieval.law_names()
    )
    with pytest.raises(_Aborted) as aborted:
        servicer.SearchArticles(
            canon_pb2.SearchArticlesRequest(query="噪音"),
            _Context(remaining=0.05),  # type: ignore[arg-type]
        )

    assert aborted.value.status.code == grpc.StatusCode.DEADLINE_EXCEEDED
    assert _reason(aborted.value.status) == errors.READ_TIMEOUT


def test_an_exhausted_pool_is_busy(dsn: str) -> None:
    pool = ConnectionPool(dsn, min_size=1, max_size=1, open=True)
    try:
        local = CanonStore.over(Connections(pool))
        servicer = build_servicer(
            local.laws, local.graph, local.retrieval, Segmenter([]), local.retrieval.law_names()
        )
        with pool.connection(), pytest.raises(_Aborted) as aborted:
            servicer.SearchArticles(
                canon_pb2.SearchArticlesRequest(query="噪音"),
                _Context(remaining=0.2),  # type: ignore[arg-type]
            )
        assert aborted.value.status.code == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert _reason(aborted.value.status) == errors.BUSY
    finally:
        pool.close()


def test_a_full_embed_slot_still_returns_lexical_hits(
    conn: psycopg.Connection[tuple[object, ...]], store: CanonStore, sample: None
) -> None:
    assert projection.rebuild(conn) > 0
    conn.commit()
    embedder = _CountingEmbedder()
    servicer = build_servicer(
        store.laws,
        store.graph,
        store.retrieval,
        Segmenter(store.retrieval.lexicon()),
        store.retrieval.law_names(),
        embedder=embedder,  # type: ignore[arg-type]
        tuning=SearchTuning(embed_slots=1),
    )
    assert servicer.search._embed_slots is not None
    assert servicer.search._embed_slots.acquire(blocking=False)

    response = servicer.SearchArticles(
        canon_pb2.SearchArticlesRequest(query="空氣污染"),
        _Context(remaining=5),  # type: ignore[arg-type]
    )

    assert embedder.calls == 0
    assert response.hits


def test_an_embed_slot_is_released_for_the_next_query(store: CanonStore) -> None:
    embedder = _CountingEmbedder()
    servicer = build_servicer(
        store.laws,
        store.graph,
        store.retrieval,
        Segmenter([]),
        store.retrieval.law_names(),
        embedder=embedder,  # type: ignore[arg-type]
        tuning=SearchTuning(embed_slots=1),
    )
    request = canon_pb2.SearchArticlesRequest(query="噪音")
    servicer.SearchArticles(request, _Context(remaining=5))  # type: ignore[arg-type]
    servicer.SearchArticles(request, _Context(remaining=5))  # type: ignore[arg-type]
    assert embedder.calls == 2
