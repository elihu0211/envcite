"""罰則的跨單元連結：罰則條文處罰的是它引用的別條，而抽取是逐單元進行的。

罰則條文（「違反第七條第一項規定者，處…罰鍰」）在自己的單元裡看不到受罰的陳述，所以抽取者只標出罰則
本身，等所有單元抽完，再由這裡把它連到原文引用的條文裡的陳述：候選只有一個就直接連，這一步不呼叫
模型；有幾個才請抽取模型從候選裡挑一個。連結不是接受：連好的罰則仍要過引用閘門（受罰的陳述是否真的在
原文引用的條文裡）、評審團等，與其他陳述一樣。

找不到候選（被引用的條文不在語料裡、或還沒有陳述）、或模型挑不出來的罰則，丟掉並記下原因。
一個單元若在連結之前就中斷，重跑時它已有陳述而被略過，待連結的罰則會遺失；換 `agent_version` 重抽。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from env_law_ontology.annotate import AnnotationError, load
from env_law_ontology.verification.gates import Conn, _unit, citations
from env_law_ontology.verification.jsonutil import object_from
from env_law_ontology.verification.models import Model, ModelError

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
        "  JOIN corpus.provision AS p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND s.kind = 'prescriptive'"
        "   AND s.deontic IN ('obligation', 'prohibition') AND s.review <> 'rejected'"
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
    listed = "\n".join(f"[{sid}] {path}：{_unit(conn, sid).quoted}" for sid, path in candidates)
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
