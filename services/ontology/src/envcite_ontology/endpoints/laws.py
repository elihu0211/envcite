"""法規與條文的端點：取法規、列條文、列法規清單、依 IRI 取條文、列違憲失效。"""

import grpc

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite_grpc import remaining_seconds
from envcite_ontology import errors, identifiers
from envcite_ontology.endpoints.common import SERVICE_LOG, category_prefix_filter, log_call
from envcite_ontology.identifiers import jurisdiction_filter, law_key
from envcite_ontology.store.ports import LawReader
from envcite_ontology.store.records import LawKey

_RULING_KINDS = {
    "interpretation": pb.RULING_KIND_INTERPRETATION,
    "judgment": pb.RULING_KIND_JUDGMENT,
}

_HEADING_KINDS = {
    "part": pb.HEADING_KIND_PART,
    "chapter": pb.HEADING_KIND_CHAPTER,
    "section": pb.HEADING_KIND_SECTION,
    "subsection": pb.HEADING_KIND_SUBSECTION,
    "item_heading": pb.HEADING_KIND_ITEM_HEADING,
}


class LawApi:
    """法規與條文的端點；請求在這裡驗證完，之後只用型別明確的值。"""

    def __init__(self, laws: LawReader) -> None:
        self._laws = laws

    def _request_key(self, identifier: pb.LawIdentifier, as_of: str) -> LawKey:
        """必填的自然鍵與 `as_of` 一次驗證，違規一次回報。"""
        v = errors.Validator()
        key = law_key(self._laws, identifier, v)
        v.date("as_of", as_of)
        v.check()
        return key

    def get_law(
        self, request: pb.GetLawRequest, context: grpc.ServicerContext
    ) -> pb.GetLawResponse:
        """依自然鍵取得一部法規；不存在回 `LAW_NOT_FOUND`。"""
        log_call(SERVICE_LOG, context, "GetLaw", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        law = self._laws.get_law(key, as_of=request.as_of, timeout=remaining_seconds(context))
        if law is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        return pb.GetLawResponse(
            law=pb.Law(
                identifier=pb.LawIdentifier(
                    jurisdiction=law.jurisdiction,
                    id_scheme=law.id_scheme,
                    local_id=law.local_id,
                ),
                iri=law.iri,
                title=law.title,
                language=law.language,
                date_document=law.date_document,
            )
        )

    def list_articles(
        self, request: pb.ListArticlesRequest, context: grpc.ServicerContext
    ) -> pb.ListArticlesResponse:
        """依閱讀順序列出一部法規的條文。"""
        log_call(SERVICE_LOG, context, "ListArticles", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        timeout = remaining_seconds(context)
        articles = self._laws.list_articles(key, as_of=request.as_of, timeout=timeout)
        if articles is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        headings = self._laws.list_headings(key, as_of=request.as_of, timeout=timeout) or []
        return pb.ListArticlesResponse(
            articles=[
                pb.Article(iri=a.iri, number=a.number, sort_key=a.sort_key) for a in articles
            ],
            headings=[
                pb.StructureHeading(
                    kind=_HEADING_KINDS.get(h.kind, pb.HEADING_KIND_UNSPECIFIED),
                    heading_no=h.heading_no,
                    title=h.title,
                    sort_key=h.sort_key,
                    parent_index=h.parent_index,
                )
                for h in headings
            ],
        )

    def list_laws(
        self, request: pb.ListLawsRequest, context: grpc.ServicerContext
    ) -> pb.ListLawsResponse:
        """列出 `as_of` 當日有有效版本的法規摘要與授權母法關聯。"""
        log_call(SERVICE_LOG, context, "ListLaws", request.jurisdiction or "all")
        v = errors.Validator()
        v.date("as_of", request.as_of)
        jurisdiction = jurisdiction_filter(self._laws, request.jurisdiction, v)
        category_prefix = category_prefix_filter(request.category_prefix, v)
        v.check()

        laws = self._laws.list_laws(
            as_of=request.as_of,
            jurisdiction=jurisdiction,
            category_prefix=category_prefix,
            timeout=remaining_seconds(context),
        )
        items: list[pb.LawSummary] = []
        for item in laws:
            parent = (
                pb.LawIdentifier(
                    jurisdiction=item.parent_jurisdiction,
                    id_scheme=item.parent_id_scheme,
                    local_id=item.parent_local_id,
                )
                if item.parent_jurisdiction and item.parent_id_scheme and item.parent_local_id
                else None
            )
            items.append(
                pb.LawSummary(
                    identifier=pb.LawIdentifier(
                        jurisdiction=item.jurisdiction,
                        id_scheme=item.id_scheme,
                        local_id=item.local_id,
                    ),
                    iri=item.iri,
                    title=item.title,
                    norm_kind=item.norm_kind,
                    date_document=item.date_document,
                    parent_identifier=parent,
                    document_type=item.document_type,
                    child_count=item.child_count,
                    parent_count=item.parent_count,
                    guidance_count=item.guidance_count,
                )
            )
        return pb.ListLawsResponse(laws=items)

    def get_article_by_iri(
        self, request: pb.GetArticleByIriRequest, context: grpc.ServicerContext
    ) -> pb.GetArticleByIriResponse:
        """依條文版本的標準 IRI 取得單一條文全文與中繼資料。"""
        iri = request.iri.strip()
        log_call(SERVICE_LOG, context, "GetArticleByIri", iri or "<empty>")
        v = errors.Validator()
        if not iri:
            v.add("iri", errors.EMPTY, "iri is required")
        v.check()
        record = self._laws.get_article_by_iri(iri, timeout=remaining_seconds(context))
        if record is None:
            raise errors.not_found("NOT_FOUND", f"article not found: {iri}")
        return pb.GetArticleByIriResponse(
            article=pb.ArticleDetail(
                iri=record.iri,
                identifier=pb.LawIdentifier(
                    jurisdiction=record.jurisdiction,
                    id_scheme=record.id_scheme,
                    local_id=record.local_id,
                ),
                law_title=record.law_title,
                number=record.number,
                content=record.content,
                language=record.language,
                date_document=record.date_document,
                norm_kind=record.norm_kind,
                document_type=record.document_type,
                repealed_on=record.repealed_on or "",
            )
        )

    def list_invalidations(
        self, request: pb.ListInvalidationsRequest, context: grpc.ServicerContext
    ) -> pb.ListInvalidationsResponse:
        """列出 `as_of` 當日因違憲而失效的條文單元。"""
        log_call(SERVICE_LOG, context, "ListInvalidations", identifiers.subject(request.identifier))
        key = self._request_key(request.identifier, request.as_of)
        found = self._laws.list_invalidations(
            key,
            as_of=request.as_of,
            article_number=request.article_number,
            timeout=remaining_seconds(context),
        )
        if found is None:
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        return pb.ListInvalidationsResponse(
            invalidations=[
                pb.Invalidation(
                    ruling_no=i.ruling_no,
                    ruling_kind=_RULING_KINDS[i.ruling_kind],
                    decided_on=i.decided_on,
                    source_url=i.source_url,
                    excerpt=i.excerpt,
                    path=i.path,
                    invalid_from=i.invalid_from,
                    partial=i.partial,
                )
                for i in found
            ]
        )
