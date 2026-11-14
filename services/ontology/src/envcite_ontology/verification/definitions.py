"""用語定義的抽取：只處理「本法用語，定義如下」之下的款，其餘條文不增加任何模型呼叫。

立法定義只及於本法，所以用語與它涵蓋的成員都寫進該法自己的詞彙方案（`def.<pcode>`）。
陳述抽取讀這份用語清單，要件以 `value_concept` 指名其中一個用語，兩邊的代碼從源頭就一致，
不靠事後對應。

模型不負責錨點：引文、用語與每個成員的中文名都必須逐字出現在款的原文，否則丟掉。
載入狀態是 extracted；確認由定義自己的驗證閘門決定（`definition_gates`），沒通過的不進推論。
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.loader import Loaded, load
from envcite_ontology.db import Conn
from envcite_ontology.verification import prompts
from envcite_ontology.verification.context import ancestors_text
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError

#: 條文的用語定義項：「本法用語，定義如下」「本法所用名詞，定義如下」。
DEFINITION_CLAUSE = re.compile(r"(用語|用詞|名詞)[，,、]?\s*(定義|意義)如下")

_CODE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_REPAIRS = 2


@dataclass(frozen=True)
class ExtractedDefinitions:
    """一款用語定義的抽取結果；`loaded` 為 None 表示什麼都沒載入，原因在 `dropped`。"""

    loaded: Loaded | None
    #: 丟掉的輸出與原因
    dropped: tuple[str, ...]


def scheme_code(law: str) -> str:
    """法規自己的用語方案代碼。"""
    return f"def.{law}"


def is_definition_unit(conn: Conn, provision_id: int) -> bool:
    """單元是用語定義項：上層條文含「用語，定義如下」。"""
    return DEFINITION_CLAUSE.search(ancestors_text(conn, provision_id)) is not None


def definition_paths(conn: Conn, law: str) -> list[str]:
    """法規現行、有文字、屬於用語定義項的單元路徑，依閱讀順序。"""
    rows = conn.execute(
        "SELECT p.id, p.path FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.parent_id IS NOT NULL"
        "   AND EXISTS (SELECT 1 FROM corpus.provision_version AS v"
        "                WHERE v.provision_id = p.id AND v.superseded_at IS NULL"
        "                  AND v.content IS NOT NULL)"
        " ORDER BY p.sort_key, p.path",
        (law,),
    ).fetchall()
    return [str(path) for pid, path in rows if is_definition_unit(conn, int(str(pid)))]


def defined_terms(conn: Conn, law: str) -> dict[str, str]:
    """該法已有定義的用語：代碼 → 名稱，供陳述抽取指名。"""
    rows = conn.execute(
        "SELECT DISTINCT k.code, k.name FROM annotation.term_definition AS t"
        "  JOIN annotation.assertion AS tc ON tc.id = t.id"
        "  JOIN vocab.concept AS k ON k.id = t.term_concept_id"
        "  JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id"
        " WHERE s.code = %s AND tc.confirm_state <> 'rejected' ORDER BY k.code",
        (scheme_code(law),),
    )
    return {str(code): str(name) for code, name in rows}


def issues_of(answer: Mapping[str, Any], text: str) -> list[str]:
    """載入前的確定性檢查：引文、用語與成員逐字在原文，代碼合式；只查結構，不查對錯。"""
    items = answer.get("definitions")
    if not isinstance(items, list) or not items:
        return ["沒有 definitions 陣列，或陣列是空的。"]
    issues: list[str] = []
    for i, d in enumerate(items):
        where = f"definitions[{i}]"
        if not isinstance(d, Mapping):
            issues.append(f"{where} 必須是物件。")
            continue
        if str(d.get("quote", "")) not in text or not d.get("quote"):
            issues.append(f"{where}.quote 不在原文裡。")
        term = str(d.get("term", ""))
        if not term or term not in text:
            issues.append(f"{where}.term「{term}」不在原文裡。")
        if not _CODE.match(str(d.get("code", ""))):
            issues.append(f"{where}.code 必須是英文小寫與底線。")
        members = d.get("members")
        if not isinstance(members, list):
            issues.append(f"{where}.members 必須是陣列（沒有成員就給空陣列）。")
            continue
        for j, m in enumerate(members):
            label = str(m.get("label", "")) if isinstance(m, Mapping) else ""
            code = str(m.get("code", "")) if isinstance(m, Mapping) else ""
            if not label or label not in text:
                issues.append(f"{where}.members[{j}].label「{label}」不在原文裡。")
            if not _CODE.match(code):
                issues.append(f"{where}.members[{j}].code 必須是英文小寫與底線。")
            if code == str(d.get("code", "")):
                issues.append(f"{where}.members[{j}] 與用語同一個代碼。")
    return issues


def _unit(conn: Conn, law: str, path: str) -> tuple[int, str]:
    row = conn.execute(
        "SELECT p.id, v.content FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
        "   AND v.content IS NOT NULL ORDER BY v.id DESC LIMIT 1",
        (law, path),
    ).fetchone()
    if row is None:
        raise LookupError(f"no current text for {law} {path}")
    return int(str(row[0])), str(row[1])


def extract_definitions(
    conn: Conn,
    model: Model,
    law: str,
    path: str,
    *,
    agent: str | None = None,
    agent_version: str,
) -> ExtractedDefinitions:
    """抽取一款的用語定義並載入（extracted）。有問題先請模型修有限輪，修不好才丟。"""
    provision_id, text = _unit(conn, law, path)
    prompt = prompts.definitions(
        law=law, path=path, text=text, context=ancestors_text(conn, provision_id)
    )
    try:
        raw = model.complete(prompt)
    except ModelError as e:
        return ExtractedDefinitions(None, (f"<model error: {e}>"[:300],))
    answer = object_from(raw)
    for _ in range(MAX_REPAIRS):
        if answer is None:
            break
        issues = issues_of(answer, text)
        if not issues:
            break
        try:
            reply = model.complete(prompts.repair(prompt=prompt, previous=raw, issues=issues))
        except ModelError:
            break
        again = object_from(reply)
        if again is None:
            break
        raw, answer = json.dumps(again, ensure_ascii=False), again
    if answer is None:
        return ExtractedDefinitions(None, ("<unparseable model output>",))

    concepts: dict[str, str] = {}
    definitions: list[dict[str, Any]] = []
    dropped: list[str] = []
    for i, d in enumerate(answer.get("definitions") or []):
        if not isinstance(d, Mapping) or issues_of({"definitions": [d]}, text):
            dropped.append(f"definitions[{i}]: 逐字檢查或代碼不合")
            continue
        members = [m for m in d["members"] if isinstance(m, Mapping)]
        if not members:
            dropped.append(f"definitions[{i}]: 沒有明文列舉的成員，不算可用的定義")
            continue
        concepts[str(d["code"])] = str(d["term"])
        for m in members:
            concepts[str(m["code"])] = str(m["label"])
        quote = str(d["quote"])
        start = text.find(quote)
        definitions.append(
            {
                "id": f"d{i}",
                "law": law,
                "path": path,
                "text": text,
                "span": [start, start + len(quote)],
                "term": str(d["code"]),
                "definition": quote,
                "members": [str(m["code"]) for m in members],
            }
        )
    if not definitions:
        return ExtractedDefinitions(None, tuple(dropped) or ("<no usable definition>",))
    doc = {
        "derivation": {
            "method": "llm",
            "agent": agent or model.name,
            "agent_version": agent_version,
        },
        "review": "extracted",
        "scheme": {"code": scheme_code(law), "title": f"{law} 本法用語", "law": law},
        "concepts": [{"code": c, "label": label} for c, label in concepts.items()],
        "definitions": definitions,
    }
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        return ExtractedDefinitions(None, (*dropped, f"<invalid document: {e}>"))
    return ExtractedDefinitions(loaded, tuple(dropped))
