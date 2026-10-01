"""抽取器：由語言模型從條文單元寫出標註，寫入資料庫，狀態為 extracted（env-law-docs 02-05 §5.9）。

模型只負責回答「這個單元說了什麼」，不負責錨點：每個陳述附一段引文（`quote`），這裡自己在單元原文裡
定位 span、算雜湊、填 `text`；引文不在原文裡的陳述整個丟掉。模型因此無從編造錨點，也無從標註不存在的
文字。模型是一個 `Model`：給 prompt，回字串。連到哪個模型是設定，不在這裡；抽取的準確度由驗證管線
與校準負責，不由這裡保證。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from env_law_ontology.annotate import AnnotationError, Loaded, load
from env_law_ontology.verification.jsonutil import object_from
from env_law_ontology.verification.models import Model

Conn = psycopg.Connection[tuple[object, ...]]

PROMPT = """你是法規標註器。下面是一個條文單元的原文。請只依原文，輸出一個 JSON 物件：
{{"concepts": [{{"code": "英文小寫底線的代碼", "label": "中文名稱"}}],
  "statements": [{{"id": "短識別", "quote": "原文中的一段，逐字複製", "kind":
  "prescriptive|constitutive|penalty", "deontic": "obligation|prohibition|permission 或省略",
  "slots": {{"subject|action|object|medium": {{"literal": "…"}}}},
  "conditions": [{{"role": "scope|duty", "clause": 1, "concept": "代碼", "comparator":
  "le|lt|ge|gt|eq|ne", "threshold": 數字, "unit": "單位或省略", "negated": false,
  "judgement": false}}],
  "sanction": {{"effect": "fine|order|restriction|deprivation|reputation|warning|confiscation",
  "min": 數字或省略, "max": 數字或省略, "amount_text": "原文或省略", "repeating": false,
  "punishes": "受罰的那個陳述的 id"}}}}]}}
規則：
1. 引文必須逐字出現在原文；不要輸出原文沒有的內容。
2. 只有 kind 為 prescriptive 的陳述才有 deontic；constitutive（定義、適用範圍、授權）與
   penalty（罰則）不要寫 deontic。
3. 義務（應…、不得…）的要件分兩種：scope 是誰、在什麼情形下適用；duty 是義務本身的內容，例如
   「應符合放流水標準」要有一個 duty 要件（概念「符合放流水標準」），沒有 duty 的義務無法被評估。
4. 罰則（penalty）要有 sanction，effect 只能選一個值，punishes 必須是同一個單元裡另一個陳述的
   id；受罰的陳述不在這個單元裡時，不要輸出那個罰則陳述。
5. 每個要件都要有 threshold：成立與否的要件（是否排放、是否符合標準）寫 comparator "eq"、
   threshold 1；「非」「不」用 negated: true 表示；原文有數值門檻時才寫它的數字與單位。
6. 不確定法律概念或裁量（情節重大、必要時）的要件 judgement 設為 true。
只輸出 JSON。

原文（{law} {path}）：
{text}
"""


@dataclass(frozen=True)
class Extracted:
    loaded: Loaded | None
    dropped: tuple[str, ...]  # 被丟掉的輸出：引文不在原文裡的陳述，或整份違反資料庫約束的文件


def _unit_text(conn: Conn, law: str, path: str) -> str:
    row = conn.execute(
        "SELECT v.content FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
        "   AND v.content IS NOT NULL ORDER BY v.id DESC LIMIT 1",
        (law, path),
    ).fetchone()
    if row is None:
        raise LookupError(f"no current text for {law} {path}")
    return str(row[0])


def _linkable(statements: list[dict[str, Any]], dropped: list[str]) -> list[dict[str, Any]]:
    """丟掉指向文件外的罰則與例外，連帶丟掉指向被丟掉者的陳述，直到不再變動。

    罰則與例外只能指向同一份文件裡的陳述；受罰的條文在別的單元時，這個單元連不上它。
    """
    while True:
        ids = {str(s.get("id")) for s in statements}
        kept: list[dict[str, Any]] = []
        for s in statements:
            sanction = s.get("sanction")
            if isinstance(sanction, Mapping) and str(sanction.get("punishes")) not in ids:
                dropped.append(f"{s.get('id', '?')}: sanction punishes nothing in this unit")
            elif s.get("exception_of") is not None and str(s["exception_of"]) not in ids:
                dropped.append(f"{s.get('id', '?')}: excepts nothing in this unit")
            else:
                kept.append(s)
        if len(kept) == len(statements):
            return kept
        statements = kept


def extract_unit(
    conn: Conn,
    model: Model,
    law: str,
    path: str,
    *,
    agent: str | None = None,
    agent_version: str,
    scheme: tuple[str, str] = ("env", "環境法概念"),
) -> Extracted:
    """抽取一個條文單元。`agent` 記為推導者（預設為模型的名字）；驗證者不得與它相同。"""
    text = _unit_text(conn, law, path)
    raw = model.complete(PROMPT.format(law=law, path=path, text=text))
    answer: Mapping[str, Any] | None = object_from(raw)
    if answer is None:
        return Extracted(None, ("<unparseable model output>",))
    statements: list[dict[str, Any]] = []
    dropped: list[str] = []
    for s in answer.get("statements", []):
        quote = str(s.get("quote", ""))
        start = text.find(quote) if quote else -1
        if start < 0:
            dropped.append(f"{s.get('id', '?')}: quote is not in the text")
            continue
        item = {k: v for k, v in s.items() if k != "quote"}
        item |= {"law": law, "path": path, "text": text}
        if quote != text:
            item["span"] = [start, start + len(quote)]
        statements.append(item)
    statements = _linkable(statements, dropped)
    if not statements:
        return Extracted(None, tuple(dropped))
    doc = {
        "derivation": {
            "method": "llm",
            "agent": agent or model.name,
            "agent_version": agent_version,
        },
        "review": "extracted",
        "scheme": {"code": scheme[0], "title": scheme[1]},
        "concepts": answer.get("concepts", []),
        "statements": statements,
    }
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        # 模型的輸出不受我們控制：違反約束的文件整份不寫（`load` 已回滾），記下來，不拖垮整條管線。
        return Extracted(None, (*dropped, f"<invalid document: {e}>"))
    return Extracted(loaded, tuple(dropped))
