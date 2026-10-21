"""罰則的跨單元連結：抽取逐單元進行，罰則處罰的陳述在被引用的別條，等全部抽完再連。

候選只有一個直接連（不呼叫模型），多個才請抽取模型挑；連結不是接受，仍要過各閘門。
找不到候選或模型挑不出的罰則丟掉並記因；單元在連結前中斷則待連結罰則遺失，須換 `agent_version`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from envcite_ontology.annotate import AnnotationError, load
from envcite_ontology.verification.gates import Conn, citations, unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError

CHOOSE = """下面是一則罰則條文，與它引用的條文裡的陳述（編號、所在條文與原文）。
請判斷這則罰則處罰的是違反哪一個陳述。輸出 JSON：{{"punishes": 編號}}；
都不是則 {{"punishes": null}}。只輸出 JSON。

罰則原文：
{text}

候選陳述：
{candidates}
"""


@dataclass(frozen=True)
class Linked:
    statements: int
    dropped: tuple[str, ...]


def _candidates(conn: Conn, law: str, cited: Sequence[str]) -> list[tuple[int, str]]:
    """原文引用的條文裡、現行文字上、還沒被駁回的義務與禁止陳述。"""
    rows = conn.execute(
        "SELECT s.id, p.path FROM annotation.statement AS s"
        "  JOIN annotation.assertion AS sc ON sc.id = s.id"
        "  JOIN corpus.provision AS p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND s.statement_kind = 'prescriptive'"
        "   AND s.norm_modality IN ('obligation', 'prohibition') AND sc.confirm_state <> 'rejected'"
        " ORDER BY s.id",
        (law,),
    )
    return [
        (int(sid), str(path))  # type: ignore[call-overload]
        for sid, path in rows
        if any(path == c or str(path).startswith(c + "/") for c in cited)
    ]


def _choose(
    conn: Conn, model: Model, text: str, candidates: Sequence[tuple[int, str]]
) -> int | None:
    """多個候選時請模型挑一個；挑出的必須是候選之一。"""
    listed = "\n".join(f"[{sid}] {path}：{unit_of(conn, sid).quoted}" for sid, path in candidates)
    try:
        reply = model.complete(CHOOSE.format(text=text, candidates=listed))
    except ModelError:
        return None
    answer = object_from(reply)
    chosen = (answer or {}).get("punishes")
    return chosen if isinstance(chosen, int) and chosen in {sid for sid, _ in candidates} else None


def link_penalties(conn: Conn, model: Model, pending: Sequence[Mapping[str, Any]]) -> Linked:
    """把待連結的罰則連到被引用條文的陳述並寫入；回傳寫入的陳述數與丟掉的原因。"""
    written = 0
    dropped: list[str] = []
    for doc in pending:
        statements: list[dict[str, Any]] = []
        for s in doc["statements"]:
            law, text = str(s["law"]), str(s["text"])
            found = _candidates(conn, law, citations(text))
            if not found:
                dropped.append(f"{s['id']}: no statement in the cited provisions")
                continue
            target = found[0][0] if len(found) == 1 else _choose(conn, model, text, found)
            if target is None:
                dropped.append(f"{s['id']}: could not tell which statement it punishes")
                continue
            statements.append({**s, "sanction": {**s["sanction"], "punishes_id": target}})
        ids = {str(s["id"]) for s in statements}
        kept = [
            s for s in statements if s.get("exception_of") is None or str(s["exception_of"]) in ids
        ]
        dropped += [
            f"{s['id']}: excepts a penalty that was not linked" for s in statements if s not in kept
        ]
        if not kept:
            continue
        try:
            written += load(conn, {**doc, "statements": kept}).statements
        except AnnotationError as e:
            dropped.append(f"<invalid document: {e}>")
    return Linked(written, tuple(dropped))
