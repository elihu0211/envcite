"""驗證 GetArticleByIri 依標準 IRI 索引查詢單一條文全文。"""

import grpc
import pytest

from envcite.canon.v1 import canon_pb2, canon_pb2_grpc
from tests.conftest import call_metadata, identifier

pytestmark = pytest.mark.usefixtures("sample")


def test_get_article_by_iri_returns_article_detail(
    stub: canon_pb2_grpc.CanonServiceStub,
) -> None:
    listed = stub.ListArticles(
        canon_pb2.ListArticlesRequest(identifier=identifier("O0020001")),
        metadata=call_metadata(),
    )
    assert listed.articles
    first = listed.articles[0]

    resp = stub.GetArticleByIri(
        canon_pb2.GetArticleByIriRequest(iri=first.iri),
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
    stub: canon_pb2_grpc.CanonServiceStub,
) -> None:
    with pytest.raises(grpc.RpcError) as empty_err:
        stub.GetArticleByIri(
            canon_pb2.GetArticleByIriRequest(iri="   "),
            metadata=call_metadata(),
        )
    assert empty_err.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    with pytest.raises(grpc.RpcError) as not_found_err:
        stub.GetArticleByIri(
            canon_pb2.GetArticleByIriRequest(
                iri="https://envcite/tw/law/O0020001/2020-01-01/zho/art_999"
            ),
            metadata=call_metadata(),
        )
    assert not_found_err.value.code() == grpc.StatusCode.NOT_FOUND
