"""Hybrid retrieval: one content-addressed row per passage carries both arms; the semantic arm
recalls what the words miss, and searching never fails because the embedder does."""

import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_jobkit import EndpointError, ExitCode, FatalError, ItemError, Policy
from envcite_ontology import projection
from envcite_ontology.embedding import (
    DIMENSION,
    Embedder,
    EmbeddingError,
    EndpointMisconfigured,
    EndpointUnavailable,
    PayloadTooLarge,
)
from envcite_ontology.http import USER_AGENT
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_segmenter import Segmenter
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]

ON = "2019-06-01"
TEXTS = ("排放廢水者應符合放流水標準", "管制區內不得製造噪音", "地震後應檢查建物")
#: 假的語意：每個概念一個維度，同義詞落在同一維
CONCEPTS = {"廢水": 0, "放流水": 0, "噪音": 1, "聲響": 1, "地震": 2, "建物": 3, "紅燒肉": 4}


class FakeEmbedder:
    def __init__(self, model: str = "fake-m3") -> None:
        self.model = model
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for text in texts:
            v = [0.0] * DIMENSION
            for word, axis in CONCEPTS.items():
                if word in text:
                    v[axis] += 1.0
            v[DIMENSION - 1] += 0.01  # 不讓零向量出現（餘弦距離沒有定義）
            norm = math.sqrt(sum(x * x for x in v))
            out.append([x / norm for x in v])
        return out


class Broken:
    model = "broken"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("down")


def _corpus(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(*TEXTS)))


def _search(dsn: str, query: str, embedder: FakeEmbedder | None) -> list[tuple[str, float, Any]]:
    with OntologyStore.from_dsn(dsn) as store:
        segmenter = Segmenter(store.retrieval.lexicon())
        hits = store.retrieval.search(
            segmenter.to_tsvector_input(query),
            tokenizer=segmenter.tokenizer_id,
            as_of=ON,
            embedding=None if embedder is None else embedder.embed([query])[0],
            model="" if embedder is None else embedder.model,
        )
        return [(h.number, h.lexical_score, h.semantic_similarity) for h in hits]


def test_one_row_per_passage_carries_both_arms(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]

    rows = conn.execute(
        "SELECT count(*), count(embedding), count(DISTINCT model) FROM retrieval.passage"
    ).fetchone()
    assert rows == (3, 3, 1)
    calls = embedder.calls
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    assert embedder.calls == calls  # 沒有缺的，就不再呼叫端點


def test_a_new_lexicon_retokenizes_in_place_and_keeps_the_embeddings(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]
    conn.execute("UPDATE retrieval.passage SET tokenizer = 'old-dictionary'")
    conn.commit()
    calls = embedder.calls

    assert projection.rebuild(conn, embedder) == 3  # type: ignore[arg-type]

    assert embedder.calls == calls  # 換詞典不重新嵌入
    assert (
        _scalar(conn, "SELECT count(*) FROM retrieval.passage WHERE tokenizer = 'old-dictionary'")
        == 0
    )
    assert _scalar(conn, "SELECT count(embedding) FROM retrieval.passage") == 3


def test_a_new_model_reembeds_without_touching_the_lexical_arm(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path)
    projection.rebuild(conn, FakeEmbedder())  # type: ignore[arg-type]

    assert projection.rebuild(conn, FakeEmbedder("fake-m4")) == 0  # type: ignore[arg-type]

    assert _scalar(conn, "SELECT count(*) FROM retrieval.passage WHERE model = 'fake-m4'") == 3


def test_the_semantic_arm_recalls_what_the_words_miss(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _corpus(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]

    assert _search(dsn, "聲響", None) == []  # 條文裡沒有這個詞
    (top, *_) = _search(dsn, "聲響", embedder)
    assert top[0] == "2" and top[1] == 0 and top[2] is not None and top[2] > 0.9


def test_both_arms_agreeing_ranks_first(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _corpus(conn, tmp_path)
    embedder = FakeEmbedder()
    projection.rebuild(conn, embedder)  # type: ignore[arg-type]

    hits = _search(dsn, "地震 建物", embedder)

    assert hits[0][0] == "3" and hits[0][1] > 0 and hits[0][2] is not None


def test_without_a_query_vector_only_the_words_count(conn: Conn, tmp_path: Path, dsn: str) -> None:
    _corpus(conn, tmp_path)
    projection.rebuild(conn, FakeEmbedder())  # type: ignore[arg-type]

    hits = _search(dsn, "噪音", None)

    assert [(n, s is None) for n, _, s in hits] == [("2", True)]


def test_a_failing_embedder_does_not_fail_the_search(store: OntologyStore) -> None:
    from envcite_ontology.service import build_servicer

    servicer = build_servicer(
        store.laws,
        store.graph,
        store.retrieval,
        Segmenter([]),
        Broken(),  # type: ignore[arg-type]
    )

    assert servicer.search._query_embedding("噪音") is None


def test_the_client_reads_openai_embeddings_and_checks_the_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class Reply:
        def __init__(self, body: dict[str, Any]) -> None:
            self.body = json.dumps(body).encode()

        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def ok(request: Any, timeout: float) -> Reply:
        sent.update(
            url=request.full_url,
            auth=request.get_header("Authorization"),
            user_agent=request.get_header("User-agent"),
        )
        sent["body"] = json.loads(request.data)
        return Reply(
            {
                "data": [
                    {"index": 1, "embedding": [0.0] * DIMENSION},
                    {"index": 0, "embedding": [1.0] * DIMENSION},
                ]
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", ok)
    client = Embedder("bge-m3-embedding", base_url="https://e.example/v1/", api_key="k")

    first, second = client.embed(["甲", "乙"])

    assert (first[0], second[0]) == (1.0, 0.0)  # 依 index 排回輸入順序
    assert sent["url"] == "https://e.example/v1/embeddings" and sent["auth"] == "Bearer k"
    assert sent["user_agent"] == USER_AGENT  # 預設的 Python-urllib 會被端點前的 WAF 擋下
    assert sent["body"] == {"model": "bge-m3-embedding", "input": ["甲", "乙"]}
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda r, timeout: Reply({"data": [{"index": 0, "embedding": [0.0] * 3}]}),
    )
    with pytest.raises(EmbeddingError, match="1024"):
        client.embed(["甲"])


class _Reply:
    def __init__(self, count: int) -> None:
        self.body = json.dumps(
            {"data": [{"index": i, "embedding": [float(i)] * DIMENSION} for i in range(count)]}
        ).encode()

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://e.example/v1/embeddings", code, "x", None, None)  # type: ignore[arg-type]


def test_a_413_batch_is_split_until_the_endpoint_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sizes: list[int] = []

    def reply(request: Any, timeout: float) -> _Reply:
        inputs = json.loads(request.data)["input"]
        sizes.append(len(inputs))
        if len(inputs) > 2:
            raise _http_error(413)
        return _Reply(len(inputs))

    monkeypatch.setattr(urllib.request, "urlopen", reply)
    client = Embedder("m", base_url="https://e.example/v1")

    vectors = client.embed(list("abcde"))

    assert len(vectors) == 5
    assert sizes == [5, 2, 3, 1, 2]  # 5 -> (2, 3 -> (1, 2))


def test_a_single_text_over_the_model_limit_is_truncated_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lengths: list[int] = []

    def reply(request: Any, timeout: float) -> _Reply:
        (text,) = json.loads(request.data)["input"]
        lengths.append(len(text))
        if len(text) > 1000:
            raise _http_error(413)
        return _Reply(1)

    monkeypatch.setattr(urllib.request, "urlopen", reply)
    client = Embedder("m", base_url="https://e.example/v1")

    with caplog.at_level("WARNING"):
        (vector,) = client.embed(["長" * 2000])

    assert len(vector) == DIMENSION
    assert lengths == [2000, 1500, 1125, 843]
    assert "2000" in caplog.text and "843" in caplog.text


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (503, EndpointError),
        (520, EndpointError),  # CDN 前置的端點回 520–524，同屬端點問題
        (429, EndpointError),
        (400, ItemError),
        (401, FatalError),
    ],
)
def test_each_failure_is_one_request_and_names_its_disposition(
    monkeypatch: pytest.MonkeyPatch, status: int, kind: type[Exception]
) -> None:
    calls: list[int] = []

    def reply(request: Any, timeout: float) -> _Reply:
        calls.append(len(json.loads(request.data)["input"]))
        raise _http_error(status)

    monkeypatch.setattr(urllib.request, "urlopen", reply)
    client = Embedder("m", base_url="https://e.example/v1")

    with pytest.raises(EmbeddingError, match=f"HTTP {status}") as raised:
        client.embed(["甲", "乙"])

    assert isinstance(raised.value, kind)
    assert not isinstance(raised.value, PayloadTooLarge)
    assert calls == [2]  # 不在這裡重試，也不拆批：重試是呼叫端政策的事


class Flaky(FakeEmbedder):
    """前 `failures` 次呼叫回端點錯誤，之後正常。"""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.failures:
            self.failures -= 1
            raise EndpointUnavailable("fake-m3: HTTP 520")
        return super().embed(texts)


NO_WAIT = Policy(max_attempts=3, backoff_base_seconds=0.0, open_action="wait")


def test_a_transient_endpoint_error_is_retried_and_the_projection_completes(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path)
    embedder = Flaky(failures=2)

    projection.rebuild(conn, embedder, NO_WAIT)  # type: ignore[arg-type]

    assert _scalar(conn, "SELECT count(*) FROM retrieval.passage WHERE embedding IS NULL") == 0


def test_a_batch_that_keeps_failing_is_skipped_and_embedded_on_the_next_run(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path)

    projection.rebuild(conn, Flaky(failures=NO_WAIT.max_attempts), NO_WAIT)  # type: ignore[arg-type]
    assert _scalar(conn, "SELECT count(*) FROM retrieval.passage WHERE embedding IS NULL") > 0

    projection.rebuild(conn, FakeEmbedder(), NO_WAIT)  # type: ignore[arg-type]
    assert _scalar(conn, "SELECT count(*) FROM retrieval.passage WHERE embedding IS NULL") == 0


class Misconfigured(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EndpointMisconfigured("fake-m3: HTTP 401")


def test_a_misconfigured_endpoint_stops_the_projection_with_the_fatal_code(
    conn: Conn, tmp_path: Path
) -> None:
    _corpus(conn, tmp_path)

    with pytest.raises(projection.EmbeddingStopped) as stopped:
        projection.rebuild(conn, Misconfigured(), NO_WAIT)  # type: ignore[arg-type]

    assert stopped.value.code == ExitCode.FATAL


def test_embedding_a_scope_leaves_other_laws_without_vectors(conn: Conn, tmp_path: Path) -> None:
    _corpus(conn, tmp_path)
    projection.rebuild(conn)
    (pcode,) = conn.execute("SELECT local_id FROM corpus.legal_resource").fetchone()  # type: ignore[misc]
    embedder = FakeEmbedder()

    assert projection.embed_scope(conn, embedder, ["Z9999999"]) == 0  # type: ignore[arg-type]
    assert embedder.calls == 0
    assert _scalar(conn, "SELECT count(embedding) FROM retrieval.passage") == 0

    assert projection.embed_scope(conn, embedder, [str(pcode)]) == 3  # type: ignore[arg-type]
    assert _scalar(conn, "SELECT count(embedding) FROM retrieval.passage") == 3
    assert projection.embed_scope(conn, embedder, [str(pcode)]) == 0  # type: ignore[arg-type]
