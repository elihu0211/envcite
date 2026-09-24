"""SearchArticles end to end: the service segments the query itself.

Content is indexed with a segmenter built from the same lexicon the server reads
at startup, so the test exercises the one property the RPC exists for — both
sides reach the same segmentation — rather than handing the database words.
"""

from collections.abc import Iterator

import grpc
import psycopg
import pytest
from env_law_segmenter import Segmenter

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law.server import build_server
from env_law.store import OntologyStore

from .conftest import TOKEN, auth

BASE = "https://env.law/eli/tw/law/search-act"


@pytest.fixture
def search_stub(
    conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore
) -> Iterator[ontology_pb2_grpc.OntologyServiceStub]:
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES ('search-act', %s, 'statute')",
        (BASE,),
    )
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, 'search-act', '噪音管制法', 'zh-hant', DATE '2020-01-01')",
        (f"{BASE}/v1",),
    )
    conn.commit()

    segmenter = Segmenter(store.lexicon())
    for number, content in (
        ("9", "工廠不得發出超過管制標準之噪音。"),
        ("10", "主管機關應定期檢查。"),
    ):
        iri = f"{BASE}/v1/art_{number}"
        conn.execute(
            "INSERT INTO subdivision (iri, resource_id, number, expression_iri, article_iri,"
            " content, content_tsv, tokenizer)"
            " VALUES (%s, 'search-act', %s, %s, %s, %s, to_tsvector('simple', %s), %s)",
            (
                iri,
                number,
                f"{BASE}/v1",
                iri,
                content,
                segmenter.to_tsvector_input(content),
                segmenter.tokenizer_id,
            ),
        )
    conn.commit()

    server, port = build_server(store, TOKEN, "127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield ontology_pb2_grpc.OntologyServiceStub(channel)
    server.stop(grace=None)


def test_search_finds_the_article_by_raw_text(
    search_stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    response = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音管制標準"), metadata=auth()
    )

    top = response.hits[0]
    assert (top.law_id, top.law_title, top.number) == ("search-act", "噪音管制法", "9")
    assert top.HasField("lexical_score")
    # No embedding model yet: the dense arm contributes nothing.
    assert not top.HasField("dense_distance")


def test_search_before_the_law_existed_finds_nothing(
    search_stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    response = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音", as_of="2019-12-31"), metadata=auth()
    )

    assert list(response.hits) == []


@pytest.mark.parametrize(
    "request_",
    [
        ontology_pb2.SearchArticlesRequest(query="  "),
        ontology_pb2.SearchArticlesRequest(query="噪音", limit=51),
        ontology_pb2.SearchArticlesRequest(query="噪音", as_of="yesterday"),
    ],
    ids=["blank-query", "limit-too-large", "as-of-not-a-date"],
)
def test_bad_search_is_invalid_argument(
    stub: ontology_pb2_grpc.OntologyServiceStub, request_: ontology_pb2.SearchArticlesRequest
) -> None:
    with pytest.raises(grpc.RpcError) as error:
        stub.SearchArticles(request_, metadata=auth())

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
