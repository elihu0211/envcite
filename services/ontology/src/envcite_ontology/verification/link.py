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
from typing import Any, cast

from envcite_ontology.claims import AnnotationError, load
from envcite_ontology.db import Conn
from envcite_ontology.verification.gates import unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError
from envcite_ontology.violation import within

CHOOSE = """下面是一則罰則條文，與它引用的條文裡的陳述（編號、所在條文與原文）。
請列出這則罰則處罰的、違反了就受罰的全部陳述。原文引用數條（「違反第七條或第八條」）時
可能不只一個；原文只罰引用條文的某種違反（「違反第十四條第一項未取得許可證而排放者」）時，
只列那一種。輸出 JSON：{{"punishes": [編號, ...]}}；都不是則 {{"punishes": []}}。只輸出 JSON。

罰則原文：
{text}

候選陳述：
{candidates}
"""


def penalized(conn: Conn, law: str) -> dict[str, list[str]]:
    """法規的單元 → 以「違反」引用它（或它的上層）的處罰條文，取自依賴圖。"""
    rows = conn.execute(
        "SELECT p.path, vi.cited_path FROM annotation.current_violation AS vi"
        "  JOIN corpus.provision AS p ON p.id = vi.provision_id"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = vi.provision_id"
        "   AND v.content_sha256 = vi.content_sha256 AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND vi.violation_kind = 'duty'"
        " ORDER BY p.sort_key, p.path",
        (law,),
    ).fetchall()
    units = [
        str(u)
        for (u,) in conn.execute(
            "SELECT p.path FROM corpus.provision AS p"
            "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
            " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s",
            (law,),
        )
    ]
    out: dict[str, list[str]] = {}
    for unit in units:
        by = [str(penalty) for penalty, cited in rows if within(unit, [str(cited)])]
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


def held_states(conn: Conn) -> dict[str, int]:
    """待連結罰則各狀態的份數。"""
    return {
        str(state): cast(int, n)
        for state, n in conn.execute(
            "SELECT state, count(*) FROM annotation.held_penalty_state GROUP BY state"
        )
    }


def _choose(
    conn: Conn, model: Model, text: str, candidates: Sequence[tuple[int, str]]
) -> list[int]:
    """多個候選時請模型挑出全部被處罰的；挑出的必須都是候選，有一個不是就整個不採。"""
    listed = "\n".join(f"[{sid}] {path}：{unit_of(conn, sid).quoted}" for sid, path in candidates)
    try:
        reply = model.complete(CHOOSE.format(text=text, candidates=listed))
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


def link_penalties(conn: Conn, model: Model | None) -> Linked:
    """把可連的待連結罰則寫成處罰每個被處罰義務的罰則陳述；回傳寫入的陳述數與沒連成的原因。

    沒有模型時，已確認的候選有數個的待連結罰則留著不連。
    """
    rows = conn.execute(
        "SELECT h.id, h.document FROM annotation.held_penalty AS h"
        "  JOIN annotation.held_penalty_state AS st ON st.id = h.id"
        " WHERE st.state = 'linkable' ORDER BY h.id"
    ).fetchall()
    written = 0
    dropped: list[str] = []
    ids: dict[str, int] = {}
    for hid, document in rows:
        doc = cast(Mapping[str, Any], document)
        held: Sequence[Mapping[str, Any]] = doc["statements"]
        names = f"held {hid} ({', '.join(str(s['id']) for s in held)})"
        found = [
            (cast(int, sid), str(path))
            for sid, path in conn.execute(
                "SELECT statement_id, path FROM annotation.penalty_candidates(%s)"
                " WHERE confirmed ORDER BY statement_id",
                (hid,),
            )
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
            loaded = load(conn, {**doc, "statements": copies}, held_penalty=cast(int, hid))
        except AnnotationError as e:
            dropped.append(f"{names}: <invalid document: {e}>")
            continue
        written += loaded.statements
        ids |= loaded.ids
    return Linked(written, tuple(dropped), ids)
