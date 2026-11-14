"""罰則的連結：把待連結罰則（`annotation.held_penalty`）寫成處罰已確認義務的罰則陳述。

綱要只讓罰則處罰已確認的陳述（遷移檔 0055），載入時還連不上的罰則存成待連結罰則，
等義務確認再寫；
狀態（可連、等義務、被引條文沒有義務、沒有引用）由 `annotation.held_penalty_state` 推得。
被引條文取自處罰依賴圖（`annotation.violation`，由 `envcite-sync` 寫入，見 `violation.py`）。

一則罰則條文可以處罰數個義務（「違反第七條第一項或第八條規定者」），一個罰則陳述只處罰一個，所以對
每個被處罰的義務各寫一份。已確認的候選只有一個直接連（不呼叫模型），多個才請抽取模型挑出全部被處罰的；
連結不是接受，罰則仍要過各閘門。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.loader import load
from envcite_ontology.db import Conn, fetch
from envcite_ontology.verification import prompts
from envcite_ontology.verification.gates import unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError
from envcite_ontology.verification.prompts import Candidate
from envcite_ontology.violation import within


class _CitedRow(NamedTuple):
    path: str
    cited_path: str


class _PathRow(NamedTuple):
    path: str


_CITING = (
    "SELECT p.path, vi.cited_path FROM annotation.current_violation AS vi"
    "  JOIN corpus.provision AS p ON p.id = vi.provision_id"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = vi.provision_id"
    "   AND v.content_sha256 = vi.content_sha256 AND v.superseded_at IS NULL"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND vi.violation_kind = 'duty'"
    " ORDER BY p.sort_key, p.path"
)

_UNITS = (
    "SELECT p.path FROM corpus.provision AS p"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s"
)


def penalized(conn: Conn, law: str) -> dict[str, list[str]]:
    """法規的單元 → 以「違反」引用它（或它的上層）的處罰條文，取自依賴圖。"""
    rows = fetch(conn, _CITING, (law,), _CitedRow)
    units = [r.path for r in fetch(conn, _UNITS, (law,), _PathRow)]
    out: dict[str, list[str]] = {}
    for unit in units:
        by = [r.path for r in rows if within(unit, [r.cited_path])]
        if by:
            out[unit] = list(dict.fromkeys(by))
    return out


@dataclass(frozen=True)
class Linked:
    """`link_penalties` 的結果：寫入的陳述數、沒連成的原因與寫成的陳述編號。"""

    statements: int
    #: 這次沒能寫成陳述的待連結罰則與原因
    dropped: tuple[str, ...]
    #: 寫成的陳述：文件裡的 id 加「@被處罰的陳述編號」→ 資料庫的陳述編號
    ids: Mapping[str, int] = field(default_factory=dict)


class _StateRow(NamedTuple):
    state: str
    n: int


_STATES = "SELECT state, count(*) FROM annotation.held_penalty_state GROUP BY state"


def held_states(conn: Conn) -> dict[str, int]:
    """待連結罰則各狀態的份數。"""
    return {r.state: r.n for r in fetch(conn, _STATES, None, _StateRow)}


def _choose(
    conn: Conn, model: Model, text: str, candidates: Sequence[tuple[int, str]]
) -> list[int]:
    """多個候選時請模型挑出全部被處罰的；挑出的必須都是候選，有一個不是就整個不採。"""
    listed = [Candidate(sid, path, unit_of(conn, sid).quoted) for sid, path in candidates]
    try:
        reply = model.complete(prompts.choose(text=text, candidates=listed))
    except ModelError:
        return []
    chosen = (object_from(reply) or {}).get("punishes")
    if isinstance(chosen, int | str):
        chosen = [chosen]
    if not isinstance(chosen, list):
        return []
    ids = {sid for sid, _ in candidates}
    picked = [int(c) for c in chosen if isinstance(c, int) or (isinstance(c, str) and c.isdigit())]
    return (
        sorted(set(picked)) if picked and len(picked) == len(chosen) and set(picked) <= ids else []
    )


def _copy_for(statements: Sequence[Mapping[str, Any]], target: int) -> list[dict[str, Any]]:
    """一份待連結罰則處罰 `target` 的那一份：id 加上目標，彼此的觸發與例外跟著換。"""

    def rename(ident: object) -> str:
        return f"{ident}@{target}"

    out: list[dict[str, Any]] = []
    for s in statements:
        copy: dict[str, Any] = {**s, "id": rename(s["id"])}
        if s.get("sanction") is not None:
            sanction = {**s["sanction"], "punishes_id": target}
            if sanction.get("triggers"):
                sanction["triggers"] = [
                    {**t, "statement": rename(t["statement"])} for t in sanction["triggers"]
                ]
            copy["sanction"] = sanction
        if s.get("exception_of") is not None:
            copy["exception_of"] = rename(s["exception_of"])
        out.append(copy)
    return out


class _HeldRow(NamedTuple):
    id: int
    document: Mapping[str, Any]


class _CandidateRow(NamedTuple):
    statement_id: int
    path: str


_LINKABLE = (
    "SELECT h.id, h.document FROM annotation.held_penalty AS h"
    "  JOIN annotation.held_penalty_state AS st ON st.id = h.id"
    " WHERE st.state = 'linkable' ORDER BY h.id"
)

_CANDIDATES = (
    "SELECT statement_id, path FROM annotation.penalty_candidates(%s)"
    " WHERE confirmed ORDER BY statement_id"
)


def link_penalties(conn: Conn, model: Model | None) -> Linked:
    """把可連的待連結罰則寫成處罰每個被處罰義務的罰則陳述；回傳寫入的陳述數與沒連成的原因。

    沒有模型時，已確認的候選有數個的待連結罰則留著不連。
    """
    written = 0
    dropped: list[str] = []
    ids: dict[str, int] = {}
    for pending in fetch(conn, _LINKABLE, None, _HeldRow):
        doc = pending.document
        held: Sequence[Mapping[str, Any]] = doc["statements"]
        names = f"held {pending.id} ({', '.join(str(s['id']) for s in held)})"
        found = [
            (c.statement_id, c.path) for c in fetch(conn, _CANDIDATES, (pending.id,), _CandidateRow)
        ]
        if len(found) == 1:
            targets = [found[0][0]]
        elif model is None:
            dropped.append(f"{names}: several confirmed candidates and no model to choose")
            continue
        else:
            targets = _choose(conn, model, str(held[0]["text"]), found)
        if not targets:
            dropped.append(f"{names}: could not tell which statements it punishes")
            continue
        copies = [c for t in targets for c in _copy_for(held, t)]
        try:
            loaded = load(conn, {**doc, "statements": copies}, held_penalty=pending.id)
        except AnnotationError as e:
            dropped.append(f"{names}: <invalid document: {e}>")
            continue
        written += loaded.statements
        ids |= loaded.ids
    return Linked(written, tuple(dropped), ids)
