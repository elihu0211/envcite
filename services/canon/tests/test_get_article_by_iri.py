"""驗證 GetArticleByIri 依標準 IRI 索引查詢單一條文全文。"""

import grpc
import pytest

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from tests.conftest import call_metadata, identifier

pytestmark = pytest.mark.usefixtures("sample")


def test_get_article_by_iri_returns_article_detail(
    stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    listed = stub.ListArticles(
        ontology_pb2.ListArticlesRequest(identifier=identifier("O0020001")),
        metadata=call_metadata(),
    )
    assert listed.articles
    first = listed.articles[0]

    resp = stub.GetArticleByIri(
        ontology_pb2.GetArticleByIriRequest(iri=first.iri),
        metadata=call_metadata(),
    )
    assert resp.article.iri == first.iri
    assert resp.article.number == first.number
    assert resp.article.law_title == "空氣污染防制法"
    assert resp.article.identifier.local_id == "O0020001"
    assert resp.article.norm_kind == "statute"
    assert resp.article.document_type == "法"
    assert resp.article.repealed_on == ""
    assert resp.article.content


def test_get_article_by_iri_rejects_empty_and_unknown(
    stub: ontology_pb2_grpc.OntologyServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as empty_err:
        stub.GetArticleByIri(
            ontology_pb2.GetArticleByIriRequest(iri="   "),
            metadata=call_metadata(),
        )
    assert empty_err.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    with pytest.raises(grpc.RpcError) as not_found_err:
        stub.GetArticleByIri(
            ontology_pb2.GetArticleByIriRequest(
                iri="https://envcite/tw/law/O0020001/2020-01-01/zho/art_999"
            ),
            metadata=call_metadata(),
        )
    assert not_found_err.value.code() == grpc.StatusCode.NOT_FOUND
