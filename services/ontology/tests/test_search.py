"""SearchArticles end to end: the service segments the query itself.

Content is indexed with a segmenter built from the same lexicon the server reads
at startup, so the test exercises the one property the RPC exists for — both
sides reach the same segmentation — rather than handing the database words.
"""

from collections.abc import Iterator

import grpc
import psycopg
import pytest

from env_law.ontology.v1 import ontology_pb2, ontology_pb2_grpc
from env_law_ontology.server import build_server
from env_law_ontology.store import OntologyStore
from env_law_segmenter import Segmenter

from .conftest import TOKEN, auth


@pytest.fixture
def search_stub(
    conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore
) -> Iterator[ontology_pb2_grpc.OntologyServiceStub]:
    law = conn.execute(
        "INSERT INTO corpus.legal_resource (id_scheme, local_id, kind, document_type)"
        " VALUES ('moj_pcode', 'T0000001', 'statute', '法律') RETURNING id"
    ).fetchone()
    assert law is not None
    expression = conn.execute(
        "INSERT INTO corpus.legal_expression"
        " (resource_id, language, version_date, title, text_period, text_status, authority)"
        " VALUES (%s, 'zho', DATE '2020-01-01', '噪音管制法', daterange(DATE '2020-01-01', NULL),"
        " 'available', 'official_db') RETURNING id",
        (law[0],),
    ).fetchone()
    assert expression is not None
    conn.commit()

    segmenter = Segmenter(store.lexicon())
    for number, content in (
        ("9", "工廠不得發出超過管制標準之噪音。"),
        ("10", "主管機關應定期檢查。"),
    ):
        conn.execute(
            """
            WITH p AS (
                INSERT INTO corpus.provision (resource_id, provision_type, path, sort_key)
                VALUES (%(law)s, 'article', 'art_' || %(n)s, ARRAY[%(n)s::integer])
                RETURNING id
            ), v AS (
                INSERT INTO corpus.provision_version
                       (provision_id, expression_id, resource_id, content, in_force, commencement)
                SELECT p.id, %(expr)s, %(law)s, %(content)s, daterange(DATE '2020-01-03', NULL),
                       'promulgation'
                  FROM p
                RETURNING id
            )
            INSERT INTO retrieval.article_index (provision_version_id, content_tsv, tokenizer)
            SELECT v.id, to_tsvector('simple', %(terms)s), %(tokenizer)s FROM v
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
    assert (top.law_id, top.law_title, top.number) == ("T0000001", "噪音管制法", "9")
    assert top.lexical_score > 0


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


def test_a_different_tokenizer_matches_nothing(
    search_stub: ontology_pb2_grpc.OntologyServiceStub, store: OntologyStore
) -> None:
    # Lexemes from another segmenter are not the same lexemes: fewer rows, not wrong ones.
    assert store.search("噪音", tokenizer="another-segmenter", as_of="2021-01-01") == []
