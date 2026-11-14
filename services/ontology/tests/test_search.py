"""SearchArticles end to end: the service segments the query itself.

Content is indexed with a segmenter built from the same lexicon the server reads
at startup, so the test exercises the one property the RPC exists for — both
sides reach the same segmentation — rather than handing the database words.
"""

from collections.abc import Iterator

import grpc
import psycopg
import pytest

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from envcite_ontology.server import build_server
from envcite_ontology.store.ontology_store import OntologyStore
from envcite_segmenter import Segmenter

from .conftest import call_metadata


@pytest.fixture
def search_stub(
    conn: psycopg.Connection[tuple[object, ...]], store: OntologyStore
) -> Iterator[ontology_pb2_grpc.OntologyServiceStub]:
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

    segmenter = Segmenter(store.retrieval.lexicon())
    for number, content in (
        ("9", "工廠不得發出超過管制標準之噪音。"),
        ("10", "主管機關應定期檢查。"),
    ):
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

    server, port = build_server(store, "127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield ontology_pb2_grpc.OntologyServiceStub(channel)
    server.stop(grace=None)


def test_search_finds_the_article_by_raw_text(
    search_stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    response = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音管制標準"), metadata=call_metadata()
    )

    top = response.hits[0]
    assert (
        top.identifier.jurisdiction,
        top.identifier.id_scheme,
        top.identifier.local_id,
        top.law_title,
        top.number,
    ) == ("TW-CENTRAL", "moj_pcode", "T0000001", "噪音管制法", "9")
    assert top.lexical_score > 0


def test_the_query_text_is_not_written_to_the_log(
    search_stub: ontology_pb2_grpc.OntologyServiceStub, caplog: pytest.LogCaptureFixture
) -> None:
    # 查詢是客戶的案件內容；日誌只該有它的長度。
    with caplog.at_level("INFO", logger="envcite_ontology.service"):
        search_stub.SearchArticles(
            ontology_pb2.SearchArticlesRequest(query="噪音管制標準"), metadata=call_metadata()
        )

    assert "噪音管制標準" not in caplog.text
    assert "query_chars=6" in caplog.text


def test_search_before_the_law_existed_finds_nothing(
    search_stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    response = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音", as_of="2019-12-31"),
        metadata=call_metadata(),
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
        stub.SearchArticles(request_, metadata=call_metadata())

    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_a_different_tokenizer_matches_nothing(
    search_stub: ontology_pb2_grpc.OntologyServiceStub, store: OntologyStore
) -> None:
    # Lexemes from another segmenter are not the same lexemes: fewer rows, not wrong ones.
    assert store.retrieval.search("噪音", tokenizer="another-segmenter", as_of="2021-01-01") == []


def test_term_coverage_is_the_share_of_query_terms_the_article_holds(
    search_stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    # 查詢切成 噪音 音管 管制 制標 標準；第 9 條有「噪音」「管制」「制標」「標準」，沒有「音管」。
    response = search_stub.SearchArticles(
        ontology_pb2.SearchArticlesRequest(query="噪音管制標準"), metadata=call_metadata()
    )

    assert response.hits[0].term_coverage == pytest.approx(0.8)
