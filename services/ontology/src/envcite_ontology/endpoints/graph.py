"""法規圖譜的端點：以特定法規為焦點（或依分類總覽）的多層授權知識圖譜。"""

import grpc

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite_grpc import remaining_seconds
from envcite_ontology import errors, identifiers
from envcite_ontology.endpoints.common import SERVICE_LOG, category_prefix_filter, log_call
from envcite_ontology.identifiers import law_key
from envcite_ontology.store.ports import LawGraphReader, LawReader
from envcite_ontology.store.records import LawGraphRecord

DEFAULT_GRAPH_DEPTH = 2
MAX_GRAPH_DEPTH = 4
DEFAULT_GRAPH_CHILD_LIMIT = 20
MAX_GRAPH_CHILD_LIMIT = 60

_EDGE_KINDS = {
    "delegation": pb.GRAPH_EDGE_KIND_DELEGATION,
    "sub_delegation": pb.GRAPH_EDGE_KIND_SUB_DELEGATION,
}

_VIOLATION_KINDS = {
    "duty": pb.VIOLATION_REFERENCE_KIND_DUTY,
    "delegated_rule": pb.VIOLATION_REFERENCE_KIND_DELEGATED_RULE,
}


class GraphApi:
    """法規圖譜的端點。"""

    def __init__(self, laws: LawReader, graph: LawGraphReader) -> None:
        self._laws = laws
        self._graph = graph

    def get_law_graph(
        self, request: pb.GetLawGraphRequest, context: grpc.ServicerContext
    ) -> pb.GetLawGraphResponse:
        """取得以特定法規為焦點（或依分類總覽）的多層授權知識圖譜。"""
        has_id = request.HasField("identifier")
        subject = (
            identifiers.subject(request.identifier)
            if has_id
            else f"overview:{request.category_prefix or 'all'}"
        )
        log_call(SERVICE_LOG, context, "GetLawGraph", subject)

        v = errors.Validator()
        key = law_key(self._laws, request.identifier, v) if has_id else None
        v.date("as_of", request.as_of)
        category_prefix = category_prefix_filter(request.category_prefix, v)
        upstream_depth = _bounded_int(
            request.upstream_depth,
            default=DEFAULT_GRAPH_DEPTH,
            max_value=MAX_GRAPH_DEPTH,
            field="upstream_depth",
            v=v,
        )
        downstream_depth = _bounded_int(
            request.downstream_depth,
            default=DEFAULT_GRAPH_DEPTH,
            max_value=MAX_GRAPH_DEPTH,
            field="downstream_depth",
            v=v,
        )
        child_limit = _bounded_int(
            request.child_limit,
            default=DEFAULT_GRAPH_CHILD_LIMIT,
            max_value=MAX_GRAPH_CHILD_LIMIT,
            field="child_limit",
            v=v,
        )
        v.check()

        graph = self._graph.get_law_graph(
            key,
            as_of=request.as_of,
            category_prefix=category_prefix,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
            child_limit=child_limit,
            child_document_type=request.child_document_type,
            timeout=remaining_seconds(context),
        )
        if graph is None:
            assert key is not None
            raise identifiers.not_found(self._laws, key, request.as_of, context)
        return _to_graph_response(graph)


def _bounded_int(
    value: int, *, default: int, max_value: int, field: str, v: errors.Validator
) -> int:
    actual = default if value == 0 else value
    if not 0 < actual <= max_value:
        v.add(field, errors.OUT_OF_RANGE, f"{field} must be between 1 and {max_value}")
    return actual


def _to_graph_response(graph: LawGraphRecord) -> pb.GetLawGraphResponse:
    focus = (
        pb.LawIdentifier(
            jurisdiction=graph.focus_jurisdiction,
            id_scheme=graph.focus_id_scheme,
            local_id=graph.focus_local_id,
        )
        if graph.focus_jurisdiction and graph.focus_id_scheme and graph.focus_local_id
        else None
    )
    return pb.GetLawGraphResponse(
        focus_identifier=focus,
        nodes=[
            pb.LawGraphNode(
                identifier=pb.LawIdentifier(
                    jurisdiction=n.jurisdiction,
                    id_scheme=n.id_scheme,
                    local_id=n.local_id,
                ),
                iri=n.iri,
                title=n.title,
                norm_kind=n.norm_kind,
                document_type=n.document_type,
                date_document=n.date_document,
                hierarchy_tier=n.hierarchy_tier,
                total_children=n.total_children,
                truncated_children=n.truncated_children,
                article_count=n.article_count,
                invalidation_count=n.invalidation_count,
                guidance_count=n.guidance_count,
            )
            for n in graph.nodes
        ],
        edges=[
            pb.LawGraphEdge(
                source=pb.LawIdentifier(
                    jurisdiction=e.source_jurisdiction,
                    id_scheme=e.source_id_scheme,
                    local_id=e.source_local_id,
                ),
                target=pb.LawIdentifier(
                    jurisdiction=e.target_jurisdiction,
                    id_scheme=e.target_id_scheme,
                    local_id=e.target_local_id,
                ),
                kind=_EDGE_KINDS.get(e.kind, pb.GRAPH_EDGE_KIND_UNSPECIFIED),
                anchor_articles=e.anchor_articles,
                all_rooted=e.all_rooted,
            )
            for e in graph.edges
        ],
        violation_links=[
            pb.ArticleViolationLink(
                penalty_article=v.penalty_article,
                penalty_path=v.penalty_path,
                kind=_VIOLATION_KINDS.get(v.kind, pb.VIOLATION_REFERENCE_KIND_UNSPECIFIED),
                target_article=v.target_article,
                target_path=v.target_path,
            )
            for v in graph.violation_links
        ],
        guidance_links=[
            pb.GuidanceLink(
                article_number=g.article_number,
                path=g.path,
                document_no=g.document_no,
                issuer=g.issuer,
                issued_on=g.issued_on,
                subject=g.subject,
                source_url=g.source_url,
            )
            for g in graph.guidance_links
        ],
        child_groups=[
            pb.ChildGroupCount(document_type=cg.document_type, count=cg.count)
            for cg in graph.child_groups
        ],
    )
