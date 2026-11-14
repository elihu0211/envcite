"""法規圖譜的版面：選出要顯示的節點、分層、組邊與子節點分組；純函式，不碰資料庫。"""

import re
from collections import Counter, deque
from collections.abc import Sequence
from typing import NamedTuple

from envcite_ontology.store.records import (
    ChildGroupCountRecord,
    LawGraphEdgeRecord,
    LawGraphNodeRecord,
)


class NodeRow(NamedTuple):
    """節點明細的一列：基本資料與條數、失效數、函釋數。"""

    id: int
    jurisdiction: str
    id_scheme: str
    local_id: str
    iri: str
    title: str
    norm_kind: str
    document_type: str
    date_document: str
    article_count: int
    invalidation_count: int
    guidance_count: int


class EdgeRow(NamedTuple):
    """授權邊明細的一列：兩端、被引的條文路徑，與是否都有所依據的條文。"""

    law_id: int
    command_id: int
    anchors: list[str] | None
    all_rooted: bool


_CITED_PATH_RE = re.compile(r"^art_(\d+)(?:-(\d+))?(?:/para_(\d+))?(?:/pt_(\d+))?$")


def format_cited_path(path: str) -> str:
    """把標準化路徑（如 `art_42/para_2`）轉成人可讀之法條引述字串。"""
    m = _CITED_PATH_RE.match(path)
    if m is None:
        return path
    art, sub, para, pt = m.groups()
    out = f"第 {art} 條"
    if sub:
        out += f"之 {sub}"
    if para:
        out += f" 第 {para} 項" if sub else f"第 {para} 項"
    if pt:
        out += f"第 {pt} 款"
    return out


def compute_tiers(
    res_info: dict[int, tuple[str, str, str]],
    children_of: dict[int, list[int]],
    parents_of: dict[int, list[int]],
) -> dict[int, int]:
    """依凱爾森位階從憲法(0)→法律(1)→第一層命令(2)→再授權子規則(3)計層。"""
    tiers: dict[int, int] = {}
    queue: deque[int] = deque()
    for target_kind, tier in (("constitution", 0), ("statute", 1)):
        for rid, (nk, _, _) in res_info.items():
            if nk == target_kind:
                tiers[rid] = tier
                queue.append(rid)

    while queue:
        src = queue.popleft()
        next_tier = 2 if tiers[src] <= 1 else 3
        for dst in children_of.get(src, ()):
            if dst not in res_info or res_info[dst][0] != "regulation":
                continue
            if dst not in tiers:
                tiers[dst] = next_tier
                queue.append(dst)

    for rid, (nk, _, _) in res_info.items():
        if rid not in tiers:
            tiers[rid] = 3 if (nk == "regulation" and parents_of.get(rid)) else 2
    return tiers


def select_subgraph_nodes(
    *,
    focus_id: int | None,
    res_info: dict[int, tuple[str, str, str]],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    category_prefix: str | None,
    upstream_depth: int,
    downstream_depth: int,
    child_limit: int,
    child_document_type: str,
) -> set[int]:
    """要顯示的節點：以 `focus_id` 為焦點往上、往下走幾層；沒有焦點是分類總覽。"""
    if focus_id is None:
        return select_overview_nodes(
            res_info=res_info,
            parents_of=parents_of,
            children_of=children_of,
            category_prefix=category_prefix,
            upstream_depth=upstream_depth,
            downstream_depth=downstream_depth,
            child_limit=child_limit,
            child_document_type=child_document_type,
        )
    included: set[int] = {focus_id}
    walk_upstream(focus_id, parents_of, upstream_depth, included)
    walk_downstream(
        focus_id,
        children_of,
        res_info,
        downstream_depth,
        child_limit,
        child_document_type,
        included,
    )
    return included


def walk_upstream(
    focus_id: int,
    parents_of: dict[int, list[int]],
    depth: int,
    included: set[int],
) -> None:
    """從焦點往上收授權母法，最多 `depth` 層，收進 `included`。"""
    frontier = [focus_id]
    seen = {focus_id}
    for _ in range(depth):
        nxt: list[int] = []
        for nid in frontier:
            for pid in parents_of.get(nid, ()):
                included.add(pid)
                if pid not in seen:
                    seen.add(pid)
                    nxt.append(pid)
        frontier = nxt
        if not frontier:
            break


def walk_downstream(
    focus_id: int,
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    depth: int,
    child_limit: int,
    child_document_type: str,
    included: set[int],
) -> None:
    """從焦點往下收授權子規則，每個節點最多 `child_limit` 個，收進 `included`。

    只有第一層依 `child_document_type` 篩文件種類。
    """
    frontier = [focus_id]
    seen = {focus_id}
    for hop in range(depth):
        nxt: list[int] = []
        type_filter = child_document_type if hop == 0 else ""
        for nid in frontier:
            candidates = rank_children(children_of.get(nid, ()), children_of, res_info, type_filter)
            for cid in candidates[:child_limit]:
                included.add(cid)
                if cid not in seen:
                    seen.add(cid)
                    nxt.append(cid)
        frontier = nxt
        if not frontier:
            break


def select_overview_nodes(
    *,
    res_info: dict[int, tuple[str, str, str]],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    category_prefix: str | None,
    upstream_depth: int,
    downstream_depth: int,
    child_limit: int,
    child_document_type: str,
) -> set[int]:
    """分類總覽的節點：憲法（沒有分類篩選時）與子規則最多的樞紐法規，再向外展開。"""
    included: set[int] = set()
    if category_prefix is None:
        constitutions = sorted(
            (rid for rid, (nk, _, _) in res_info.items() if nk == "constitution"),
            key=lambda rid: res_info[rid][2],
        )
        included.update(constitutions[:4])

    matching = [
        rid
        for rid, (_, _, lid) in res_info.items()
        if category_prefix is None or lid.startswith(category_prefix)
    ]
    matching.sort(
        key=lambda rid: (
            0
            if res_info[rid][0] == "statute" and children_of.get(rid)
            else (1 if children_of.get(rid) else (2 if res_info[rid][0] == "statute" else 3)),
            -len(children_of.get(rid, ())),
            res_info[rid][2],
        )
    )
    hubs = matching[:14]
    included.update(hubs)
    per_hub = max(1, min(3, child_limit))
    expand_overview_branches(
        hubs=hubs,
        parents_of=parents_of,
        children_of=children_of,
        res_info=res_info,
        upstream_depth=upstream_depth,
        downstream_depth=downstream_depth,
        per_hub=per_hub,
        child_document_type=child_document_type,
        included=included,
    )
    return included


def expand_overview_branches(
    *,
    hubs: list[int],
    parents_of: dict[int, list[int]],
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    upstream_depth: int,
    downstream_depth: int,
    per_hub: int,
    child_document_type: str,
    included: set[int],
) -> None:
    """從樞紐往上收母法、往下收子規則，節點總數有上限，收進 `included`。"""
    if upstream_depth >= 1:
        for hid in hubs:
            for pid in parents_of.get(hid, ()):
                if len(included) < 60:
                    included.add(pid)
    for hid in hubs:
        children = rank_children(
            children_of.get(hid, ()), children_of, res_info, child_document_type
        )
        for cid in children[:per_hub]:
            if len(included) >= 56:
                return
            included.add(cid)
            if downstream_depth >= 2 and children_of.get(cid):
                sub = rank_children(children_of[cid], children_of, res_info, "")
                if sub and len(included) < 60:
                    included.add(sub[0])


def rank_children(
    raw: Sequence[int],
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
    document_type: str,
) -> list[int]:
    """子規則依它自己的子規則數由多到少、再依代碼排序；`document_type` 非空時只留該種類。"""
    filtered = [
        cid
        for cid in raw
        if cid in res_info and (not document_type or res_info[cid][1] == document_type)
    ]
    filtered.sort(key=lambda cid: (-len(children_of.get(cid, ())), res_info[cid][2]))
    return filtered


def build_node_map(
    rows: list[NodeRow],
    children_of: dict[int, list[int]],
    selected: set[int],
    tiers: dict[int, int],
) -> dict[int, LawGraphNodeRecord]:
    """節點的紀錄：基本資料，加上子規則總數與沒有顯示的數量。"""
    out: dict[int, LawGraphNodeRecord] = {}
    for row in rows:
        rid = int(row.id)
        direct = children_of.get(rid, ())
        shown = sum(1 for cid in direct if cid in selected)
        total = len(direct)
        out[rid] = LawGraphNodeRecord(
            jurisdiction=str(row.jurisdiction),
            id_scheme=str(row.id_scheme),
            local_id=str(row.local_id),
            iri=str(row.iri),
            title=str(row.title),
            norm_kind=str(row.norm_kind),
            document_type=str(row.document_type),
            date_document=str(row.date_document),
            hierarchy_tier=tiers.get(rid, 2),
            total_children=total,
            truncated_children=max(0, total - shown),
            article_count=int(row.article_count),
            invalidation_count=int(row.invalidation_count),
            guidance_count=int(row.guidance_count),
        )
    return out


def build_edges(
    rows: list[EdgeRow], node_map: dict[int, LawGraphNodeRecord]
) -> list[LawGraphEdgeRecord]:
    """兩端都在 `node_map` 裡的授權邊；命令對命令是再授權（`sub_delegation`）。"""
    edges: list[LawGraphEdgeRecord] = []
    for row in rows:
        src = node_map.get(int(row.law_id))
        dst = node_map.get(int(row.command_id))
        if src is None or dst is None:
            continue
        raw_anchors: Sequence[str] = row.anchors or ()
        edges.append(
            LawGraphEdgeRecord(
                source_jurisdiction=src.jurisdiction,
                source_id_scheme=src.id_scheme,
                source_local_id=src.local_id,
                target_jurisdiction=dst.jurisdiction,
                target_id_scheme=dst.id_scheme,
                target_local_id=dst.local_id,
                kind="sub_delegation" if src.norm_kind == "regulation" else "delegation",
                anchor_articles=tuple(format_cited_path(str(a)) for a in raw_anchors),
                all_rooted=bool(row.all_rooted),
            )
        )
    return edges


def child_groups(
    focus_id: int,
    children_of: dict[int, list[int]],
    res_info: dict[int, tuple[str, str, str]],
) -> tuple[ChildGroupCountRecord, ...]:
    """焦點的子規則依文件種類計數。"""
    counts: Counter[str] = Counter()
    for cid in children_of.get(focus_id, ()):
        if cid in res_info:
            counts[res_info[cid][1]] += 1
    return tuple(
        ChildGroupCountRecord(document_type=dt, count=cnt)
        for dt, cnt in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
