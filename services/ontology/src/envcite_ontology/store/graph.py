"""多層授權知識圖譜：讀出法規之間的授權邊、條文義務與罰則連動，與函釋錨點。"""

from collections import defaultdict
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.store import graph_layout as layout
from envcite_ontology.store.laws import LawRepository
from envcite_ontology.store.params import as_of_param, expiry, time_left
from envcite_ontology.store.ports import ReadConnections
from envcite_ontology.store.records import (
    LANGUAGE,
    ArticleViolationLinkRecord,
    GuidanceLinkRecord,
    LawGraphEdgeRecord,
    LawGraphNodeRecord,
    LawGraphRecord,
    LawKey,
)

_GRAPH_RESOURCES = """
    SELECT r.id, r.norm_kind::text, r.document_type, r.local_id
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
     WHERE e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

_GRAPH_ACTIVE_EDGES = """
    SELECT DISTINCT d.law_resource_id, d.command_resource_id
      FROM annotation.delegation_usable AS d
      JOIN corpus.legal_expression AS el ON el.resource_id = d.law_resource_id
      JOIN corpus.legal_expression AS ec ON ec.resource_id = d.command_resource_id
     WHERE d.law_resource_id IS NOT NULL
       AND el.language = %(language)s
       AND el.text_period @> %(on)s::date
       AND el.text_status = 'available'
       AND ec.language = %(language)s
       AND ec.text_period @> %(on)s::date
       AND ec.text_status = 'available'
       AND annotation.delegation_command_in_force(
               d.command_resource_id,
               d.command_provision_id,
               d.command_content_sha256,
               %(language)s,
               %(on)s::date
           )
"""

_GRAPH_NODE_DETAILS = """
    WITH req AS (
        SELECT unnest(%(ids)s::bigint[]) AS id
    ),
    enactment AS (
        SELECT resource_id, min(event_date)::text AS date_document
          FROM corpus.lifecycle_event
         WHERE event_kind = 'enactment'
           AND resource_id = ANY(%(ids)s::bigint[])
         GROUP BY resource_id
    ),
    art_cnt AS (
        SELECT req.id AS resource_id, count(*)::int AS cnt
          FROM req
         CROSS JOIN LATERAL corpus.provisions_at(req.id, %(language)s, %(on)s::date) AS p
         WHERE p.provision_type = 'article'
           AND p.status IN ('in_force', 'text_unavailable')
         GROUP BY req.id
    ),
    inv_cnt AS (
        SELECT req.id AS resource_id, count(DISTINCT i.provision_id)::int AS cnt
          FROM req
         CROSS JOIN LATERAL corpus.invalidations_at(req.id, %(language)s, %(on)s::date) AS i
         GROUP BY req.id
    ),
    guide_cnt AS (
        SELECT p.resource_id, count(DISTINCT gd.id)::int AS cnt
          FROM guidance.interpretation AS gi
          JOIN guidance.document AS gd ON gd.id = gi.document_id
          JOIN corpus.provision AS p ON p.id = gi.provision_id
          JOIN corpus.provision_version AS pv
            ON pv.provision_id = gi.provision_id
           AND pv.content_sha256 = gi.content_sha256
         WHERE p.resource_id = ANY(%(ids)s::bigint[])
           AND pv.language = %(language)s
           AND gd.issued_on <= %(on)s::date
           AND pv.superseded_at IS NULL
           AND pv.force_period @> %(on)s::date
           AND NOT EXISTS (
               SELECT 1
                 FROM guidance.cessation AS cs
                WHERE cs.document_id = gd.id
                  AND cs.ceased_on <= %(on)s::date
           )
         GROUP BY p.resource_id
    )
    SELECT r.id, r.jurisdiction::text, r.id_scheme::text, r.local_id,
           corpus.resource_iri(r.norm_kind, r.local_id), e.title,
           r.norm_kind::text, r.document_type, coalesce(en.date_document, ''),
           coalesce(ac.cnt, 0), coalesce(ic.cnt, 0), coalesce(gc.cnt, 0)
      FROM corpus.legal_resource AS r
      JOIN corpus.legal_expression AS e ON e.resource_id = r.id
      LEFT JOIN enactment AS en ON en.resource_id = r.id
      LEFT JOIN art_cnt AS ac ON ac.resource_id = r.id
      LEFT JOIN inv_cnt AS ic ON ic.resource_id = r.id
      LEFT JOIN guide_cnt AS gc ON gc.resource_id = r.id
     WHERE r.id = ANY(%(ids)s::bigint[])
       AND e.language = %(language)s
       AND e.text_period @> %(on)s::date
       AND e.text_status = 'available'
"""

_GRAPH_EDGE_DETAILS = """
    SELECT d.law_resource_id, d.command_resource_id,
           array_agg(DISTINCT d.cited_path ORDER BY d.cited_path)
               FILTER (WHERE d.cited_path <> '') AS anchors,
           bool_and(
               d.law_provision_id IS NOT NULL
               AND a.status = 'in_force'
               AND NOT EXISTS (
                   SELECT 1
                     FROM corpus.invalidations_at(
                              d.law_resource_id, %(language)s, %(on)s::date
                          ) AS inv
                    WHERE inv.version_id = a.version_id
               )
           ) AS all_rooted
      FROM annotation.delegation_usable AS d
      LEFT JOIN LATERAL corpus.provisions_at(d.law_resource_id, %(language)s, %(on)s::date) AS a
           ON a.provision_id = d.law_provision_id
     WHERE d.law_resource_id = ANY(%(ids)s::bigint[])
       AND d.command_resource_id = ANY(%(ids)s::bigint[])
       AND annotation.delegation_command_in_force(
               d.command_resource_id,
               d.command_provision_id,
               d.command_content_sha256,
               %(language)s,
               %(on)s::date
           )
     GROUP BY d.law_resource_id, d.command_resource_id
     ORDER BY d.law_resource_id, d.command_resource_id
"""

_GRAPH_VIOLATION_LINKS = """
    SELECT DISTINCT ON (p.sort_key, p.path, vi.violation_kind, vi.cited_path)
           coalesce(corpus.article_number(p.path), p.path),
           p.path,
           vi.violation_kind::text,
           coalesce(corpus.article_number(vi.cited_path), vi.cited_path),
           vi.cited_path
      FROM annotation.current_violation AS vi
      JOIN corpus.provision AS p ON p.id = vi.provision_id
      JOIN corpus.provision_version AS pv
        ON pv.provision_id = vi.provision_id
       AND pv.content_sha256 = vi.content_sha256
     WHERE p.resource_id = %(resource)s
       AND pv.language = %(language)s
       AND pv.superseded_at IS NULL
       AND pv.force_period @> %(on)s::date
     ORDER BY p.sort_key, p.path, vi.violation_kind, vi.cited_path
"""

_GRAPH_GUIDANCE_LINKS = """
    SELECT article_number, path, document_no, issuer, issued_on, subject, source_url
      FROM (
          SELECT DISTINCT ON (gd.id, coalesce(corpus.article_number(p.path), p.path))
                 coalesce(corpus.article_number(p.path), p.path) AS article_number,
                 p.path,
                 gd.document_no,
                 gd.issuer,
                 gd.issued_on::text AS issued_on,
                 left(btrim(split_part(gd.subject, '說明：', 1)), 160) AS subject,
                 coalesce(gd.source_url, '') AS source_url,
                 gd.issued_on AS issued_on_date,
                 p.sort_key AS sort_key
            FROM guidance.interpretation AS gi
            JOIN guidance.document AS gd ON gd.id = gi.document_id
            JOIN corpus.provision AS p ON p.id = gi.provision_id
            JOIN corpus.provision_version AS pv
              ON pv.provision_id = gi.provision_id
             AND pv.content_sha256 = gi.content_sha256
           WHERE p.resource_id = %(resource)s
             AND pv.language = %(language)s
             AND gd.issued_on <= %(on)s::date
             AND pv.superseded_at IS NULL
             AND pv.force_period @> %(on)s::date
             AND NOT EXISTS (
                 SELECT 1
                   FROM guidance.cessation AS cs
                  WHERE cs.document_id = gd.id
                    AND cs.ceased_on <= %(on)s::date
             )
           ORDER BY gd.id, coalesce(corpus.article_number(p.path), p.path), p.sort_key
      ) AS sub
     ORDER BY issued_on_date DESC, sort_key, document_no
     LIMIT 30
"""


class _ResourceRow(NamedTuple):
    id: int
    norm_kind: str
    document_type: str
    local_id: str


class _ActiveEdgeRow(NamedTuple):
    law_id: int
    command_id: int


class GraphRepository:
    """法規圖譜的唯讀查詢。"""

    def __init__(self, connections: ReadConnections, laws: LawRepository) -> None:
        self._connections = connections
        self._laws = laws

    def get_law_graph(
        self,
        key: LawKey | None,
        *,
        as_of: str = "",
        category_prefix: str | None = None,
        upstream_depth: int = 2,
        downstream_depth: int = 2,
        child_limit: int = 20,
        child_document_type: str = "",
        timeout: float | None = None,
    ) -> LawGraphRecord | None:
        """取得多層授權知識圖譜（焦點子圖或總覽）；指定法規無版本時為 None。"""
        deadline = expiry(timeout)
        focus_id: int | None = None
        if key is not None:
            found = self._laws.resolve(key, as_of, timeout=time_left(deadline))
            if found is None:
                return None
            focus_id = found[0]

        on = as_of_param(as_of)
        with self._connections.read_connection(timeout=time_left(deadline)) as conn:
            res_info, parents_of, children_of = self._load_graph_topology(conn, on)
            tiers = layout.compute_tiers(res_info, children_of, parents_of)
            selected = layout.select_subgraph_nodes(
                focus_id=focus_id,
                res_info=res_info,
                parents_of=parents_of,
                children_of=children_of,
                category_prefix=category_prefix,
                upstream_depth=upstream_depth,
                downstream_depth=downstream_depth,
                child_limit=child_limit,
                child_document_type=child_document_type,
            )
            return self._assemble_graph(
                conn=conn,
                focus_id=focus_id,
                selected=selected,
                res_info=res_info,
                children_of=children_of,
                tiers=tiers,
                on=on,
            )

    @staticmethod
    def _load_graph_topology(
        conn: Conn, on: str
    ) -> tuple[dict[int, tuple[str, str, str]], dict[int, list[int]], dict[int, list[int]]]:
        params = {"language": LANGUAGE, "on": on}
        res_info: dict[int, tuple[str, str, str]] = {
            int(r.id): (str(r.norm_kind), str(r.document_type), str(r.local_id))
            for r in fetch(conn, _GRAPH_RESOURCES, params, _ResourceRow)
        }
        parents_of: dict[int, list[int]] = defaultdict(list)
        children_of: dict[int, list[int]] = defaultdict(list)
        for edge in fetch(conn, _GRAPH_ACTIVE_EDGES, params, _ActiveEdgeRow):
            src, dst = int(edge.law_id), int(edge.command_id)
            children_of[src].append(dst)
            parents_of[dst].append(src)
        return res_info, parents_of, children_of

    @staticmethod
    def _assemble_graph(
        *,
        conn: Conn,
        focus_id: int | None,
        selected: set[int],
        res_info: dict[int, tuple[str, str, str]],
        children_of: dict[int, list[int]],
        tiers: dict[int, int],
        on: str,
    ) -> LawGraphRecord:
        ids = sorted(selected)
        node_map: dict[int, LawGraphNodeRecord] = {}
        edges: list[LawGraphEdgeRecord] = []
        violations: list[ArticleViolationLinkRecord] = []
        guidances: list[GuidanceLinkRecord] = []
        if ids:
            detail_params = {"ids": ids, "language": LANGUAGE, "on": on}
            node_rows = fetch(conn, _GRAPH_NODE_DETAILS, detail_params, layout.NodeRow)
            node_map = layout.build_node_map(node_rows, children_of, selected, tiers)
            edge_rows = fetch(conn, _GRAPH_EDGE_DETAILS, detail_params, layout.EdgeRow)
            edges = layout.build_edges(edge_rows, node_map)
        if focus_id is not None:
            link_params = {"resource": focus_id, "language": LANGUAGE, "on": on}
            violations = fetch(
                conn, _GRAPH_VIOLATION_LINKS, link_params, ArticleViolationLinkRecord
            )
            guidances = fetch(conn, _GRAPH_GUIDANCE_LINKS, link_params, GuidanceLinkRecord)

        focus_node = node_map.get(focus_id) if focus_id is not None else None
        child_groups = layout.child_groups(focus_id, children_of, res_info) if focus_id else ()
        nodes = tuple(
            sorted(
                node_map.values(), key=lambda n: (n.hierarchy_tier, -n.total_children, n.local_id)
            )
        )
        return LawGraphRecord(
            focus_jurisdiction=focus_node.jurisdiction if focus_node else None,
            focus_id_scheme=focus_node.id_scheme if focus_node else None,
            focus_local_id=focus_node.local_id if focus_node else None,
            nodes=nodes,
            edges=tuple(edges),
            violation_links=tuple(violations),
            guidance_links=tuple(guidances),
            child_groups=child_groups,
        )
