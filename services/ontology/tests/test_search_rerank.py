"""SearchArticles 帶 `rerank`：精排改次序、看得比 limit 深、失敗時退回原次序且查詢不失敗。

精排模型以替身取代（它怎麼打分不是這裡要證明的）；證明的是服務怎麼用它：什麼時候問、問多深、
問不到時怎麼辦。
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager

import grpc
import psycopg
import pytest

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from envcite_ontology.law_names import LawNames
from envcite_ontology.reranking import RerankError
from envcite_ontology.server import build_server
from envcite_ontology.service import OntologyServicer
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter

from .conftest import call_metadata

Stub = ontology_pb2_grpc.OntologyServiceStub
Serve = Callable[..., AbstractContextManager[Stub]]

#: 四條字面分數相同的條文（同一個詞、同樣的長度）：精排前的次序只剩版本編號，即 11、12、13、14。
ARTICLES = (
    ("11", "工廠噪音甲"),
    ("12", "工廠噪音乙"),
    ("13", "工廠噪音丙"),
    ("14", "工廠噪音丁"),
)
#: 精排替身的分數：與精排前的次序相反，丁最高。
SCORES = {"甲": 0.1, "乙": 0.2, "丙": 0.3, "丁": 0.9}


class FakeReranker:
    model = "fake-reranker"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list[str], float | None]] = []

    def score(
        self, query: str, documents: Sequence[str], *, timeout: float | None = None
    ) -> list[float]:
        self.calls.append((query, list(documents), timeout))
        if self.fail:
            raise RerankError("down")
        return [next(v for k, v in SCORES.items() if k in d) for d in documents]


@pytest.fixture
def serve(conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore) -> Iterator[Serve]:
    law = conn.execute(
        "INSERT INTO corpus.legal_resource"
        " (jurisdiction, id_scheme, local_id, norm_kind, document_type)"
        " VALUES ('TW-CENTRAL', 'moj_pcode', 'T0000001', 'statute', '法律') RETURNING id"
    ).fetchone()
    assert law is not None
    expression = conn.execute(
        "INSERT INTO corpus.legal_expression"
        " (resource_id, language, version_date, title, text_period, text_status, text_authority)"
        " VALUES (%s, 'zho', DATE '2020-01-01', '噪音管制法', daterange(DATE '2020-01-01', NULL),"
        " 'available', 'official_db') RETURNING id",
        (law[0],),
    ).fetchone()
    assert expression is not None
    conn.commit()

    segmenter = Segmenter(store.lexicon())
    for number, content in ARTICLES:
        conn.execute(
            """
            WITH p AS (
                INSERT INTO corpus.provision (resource_id, provision_type, path)
                VALUES (%(law)s, 'article', 'art_' || %(n)s)
                RETURNING id
            ), v AS (
                INSERT INTO corpus.provision_version
                        (provision_id, expression_id, resource_id, content,
                         force_period, commencement_rule)
                SELECT p.id, %(expr)s, %(law)s, %(content)s, daterange(DATE '2020-01-03', NULL),
                       'promulgation'
                  FROM p
                RETURNING id
            )
            INSERT INTO retrieval.passage (content_sha256, tokenizer, tsv)
            SELECT sha256(convert_to(%(content)s, 'UTF8')), %(tokenizer)s,
                   to_tsvector('simple', %(terms)s)
              FROM v
            """,
            {
                "law": law[0],
                "expr": expression[0],
                "n": number,
                "content": content,
                "terms": segmenter.to_tsvector_input(content),
                "tokenizer": segmenter.tokenizer_id,
            },
        )
    conn.commit()

    @contextmanager
    def serving(reranker: FakeReranker | None, candidates: int = 30) -> Iterator[Stub]:
        server, port = build_server(
            store, "127.0.0.1:0", reranker=reranker, rerank_candidates=candidates
        )
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                yield ontology_pb2_grpc.OntologyServiceStub(channel)
        finally:
            server.stop(grace=None)

    yield serving


def _numbers(response: ontology_pb2.SearchArticlesResponse) -> list[str]:
    return [h.number for h in response.hits]


def _search(stub: Stub, **fields: object) -> ontology_pb2.SearchArticlesResponse:
    return stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音", **fields),  # type: ignore[arg-type]
        metadata=call_metadata(),
        timeout=10,
    )


def test_without_the_flag_the_reranker_is_never_asked(serve: Serve) -> None:
    reranker = FakeReranker()
    with serve(reranker) as stub:
        response = _search(stub)

    assert reranker.calls == []
    assert _numbers(response) == ["11", "12", "13", "14"]
    assert not any(h.HasField("rerank_score") for h in response.hits)


def test_the_flag_reorders_by_the_reranker_and_reports_its_scores(
    serve: Serve,
) -> None:
    reranker = FakeReranker()
    with serve(reranker) as stub:
        response = _search(stub, rerank=True)

    assert _numbers(response) == ["14", "13", "12", "11"]
    assert [round(h.rerank_score, 2) for h in response.hits] == [0.9, 0.3, 0.2, 0.1]
    ((query, documents, timeout),) = reranker.calls
    assert query == "噪音" and len(documents) == 4
    assert timeout is not None and 5 < timeout < 11  # 呼叫端剩下的預算有帶下去


def test_the_reranker_sees_deeper_than_the_limit(serve: Serve) -> None:
    """limit = 1 時，兩臂的第一名是 11；精排從更深的候選裡把 14 撈上來，只回這一條。"""
    reranker = FakeReranker()
    with serve(reranker, 30) as stub:
        response = _search(stub, rerank=True, limit=1)

    assert _numbers(response) == ["14"]
    assert len(reranker.calls[0][1]) == 4


def test_the_candidate_depth_is_configurable(serve: Serve) -> None:
    reranker = FakeReranker()
    with serve(reranker, 2) as stub:
        response = _search(stub, rerank=True, limit=1)

    assert len(reranker.calls[0][1]) == 2  # 只看前兩條：11、12
    assert _numbers(response) == ["12"]


def test_a_failing_reranker_falls_back_to_the_first_stage_order_without_failing_the_query(
    serve: Serve,
) -> None:
    reranker = FakeReranker(fail=True)
    with serve(reranker) as stub:
        baseline = _search(stub, limit=2)
        response = _search(stub, rerank=True, limit=2)

    assert len(reranker.calls) == 1
    assert _numbers(response) == _numbers(baseline) == ["11", "12"]  # 條數也與沒要精排時相同
    assert not any(h.HasField("rerank_score") for h in response.hits)


def test_asking_to_rerank_without_a_reranker_is_the_same_as_not_asking(
    serve: Serve,
) -> None:
    with serve(None) as stub:
        plain = _search(stub, limit=2)
        asked = _search(stub, rerank=True, limit=2)

    assert asked == plain


def test_full_rerank_slots_fall_back_instead_of_waiting(store: OntologyStore, serve: Serve) -> None:
    class Context:
        def invocation_metadata(self) -> tuple[()]:
            return ()

        def time_remaining(self) -> float:
            return 5.0

    reranker = FakeReranker()
    servicer = OntologyServicer(
        store,
        Segmenter(store.lexicon()),
        LawNames({}),
        reranker=reranker,
        rerank_slots=0,
    )

    response = servicer.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音", rerank=True, limit=2),
        Context(),  # type: ignore[arg-type]
    )

    assert reranker.calls == []
    assert _numbers(response) == ["11", "12"]
    assert not any(h.HasField("rerank_score") for h in response.hits)
