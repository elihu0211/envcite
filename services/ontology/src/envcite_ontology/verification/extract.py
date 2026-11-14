"""抽取器：由語言模型從條文單元寫出標註，狀態為 extracted。

模型不負責錨點：每個陳述附 `quote`，span、雜湊、`text` 由這裡在原文定位；引文不在原文的陳述丟掉。
模型常把原文的字寫成異體字（污→汙），定位時兩邊一併歸一，span 仍取自原文。
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.loader import Loaded, load
from envcite_ontology.db import Conn, fetch_one
from envcite_ontology.verification import prompts
from envcite_ontology.verification.carry import carry, truncated_issues
from envcite_ontology.verification.checks.coverage import uncovered_issues, unusable
from envcite_ontology.verification.checks.fragments import (
    completeness_issues,
    fragment_issues,
    fragments,
)
from envcite_ontology.verification.checks.locate import locate
from envcite_ontology.verification.checks.sanctions import (
    criminal_penalties,
    penalized_issues,
    sanction_issues,
    settled_sanctions,
)
from envcite_ontology.verification.checks.shape import (
    borrowed_issues,
    concepts_of,
    shape_issues,
    statements_of,
    tidy,
)
from envcite_ontology.verification.context import ancestors_text
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError
from envcite_ontology.verification.statements_format import complete
from envcite_ontology.verification.subject import normalize as normalize_subjects

MAX_REPAIRS = 2


@dataclass(frozen=True)
class Counts:
    """一個單元的抽取過程計數，供比較不同抽取路徑（`verification` 的 A/B 量測）。"""

    #: 呼叫模型的次數（首次加修復）
    calls: int = 0
    #: 用掉的修復次數（0 至 `MAX_REPAIRS`）
    repairs: int = 0
    #: 語法失敗：首次輸出取不出 JSON（0 或 1）
    syntax_failures: int = 0
    #: 形狀問題：首次輸出被 `shape_issues` 指出的問題數（概念未宣告等，多為語意，不是語法）
    shape_failures: int = 0


@dataclass(frozen=True)
class Extracted:
    """一個單元的陳述抽取結果；`loaded` 為 None 表示什麼都沒載入，原因在 `dropped`。"""

    loaded: Loaded | None
    #: 丟掉的輸出：引文不在原文的陳述，或違反資料庫約束的整份文件
    dropped: tuple[str, ...]
    counts: Counts = Counts()


class _UnitTextRow(NamedTuple):
    provision_id: int
    content: str


_UNIT_TEXT = (
    "SELECT p.id, v.content FROM corpus.provision AS p"
    "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
    "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
    " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = %s AND p.path = %s"
    "   AND v.content IS NOT NULL ORDER BY v.id DESC LIMIT 1"
)


def _unit_text(conn: Conn, law: str, path: str) -> tuple[int, str]:
    """單元的 provision id 與現行文字。"""
    row = fetch_one(conn, _UNIT_TEXT, (law, path), _UnitTextRow)
    if row is None:
        raise LookupError(f"no current text for {law} {path}")
    return row.provision_id, row.content


def _located(answer: Mapping[str, Any], text: str) -> list[Mapping[str, Any]]:
    """引文在原文裡的陳述；引文不在的，後面一律丟掉，不值得請模型修。"""
    return [x for x in statements_of(answer) if locate(str(x.get("quote", "")), text) >= 0]


def _repair_issues(
    answer: Mapping[str, Any],
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
    punished_by: Sequence[str],
) -> list[str]:
    """該請模型修的問題；空表示這一版可以收。"""
    here = _located(answer, text)
    shape = shape_issues(answer, defined)
    return (
        completeness_issues(here)
        + fragment_issues(here, text)
        + borrowed_issues(here, defined)
        + uncovered_issues(
            here,
            {str(c["code"]): str(c["label"]) for c in concepts_of(answer)} | dict(defined or {}),
        )
        + truncated_issues(here, text)
        + sanction_issues(here)
        + penalized_issues(here, punished_by)
        + [
            f"陳述 {ident}：{reason}。刑罰（有期徒刑、拘役、罰金）不是行政罰，沒有對應的 effect"
            "（罰金不是 fine）：這樣的罰則不要寫成 sanction 陳述。"
            for ident, reason in criminal_penalties(here, text, context).items()
        ]
        + [
            f"陳述 {ident}：{problem}"
            for x in here
            for ident in [str(x.get("id", "?"))]
            for problem in shape.get(ident, [])
        ]
    )


def _repair(
    model: Model,
    prompt: str,
    raw: str,
    answer: Mapping[str, Any] | None,
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
    punished_by: Sequence[str],
) -> tuple[Mapping[str, Any] | None, int]:
    """有問題就請模型修，最多 `MAX_REPAIRS` 輪；回傳最後採用的回答與修復的輪數。

    修復的呼叫失敗或回不出物件就沿用現有版本，缺的由後面的丟棄與驗證處理。
    """
    repairs = 0
    for _ in range(MAX_REPAIRS):
        if answer is None:
            break
        issues = _repair_issues(answer, text, context, defined, punished_by)
        if not issues:
            break
        repairs += 1
        try:
            reply = complete(model, prompts.repair(prompt=prompt, previous=raw, issues=issues))
        except ModelError:
            break
        again = object_from(reply)
        if again is None:
            break
        raw, answer = json.dumps(again, ensure_ascii=False), tidy(again, defined)
    return answer, repairs


def _supplemented(
    answer: Mapping[str, Any], text: str, context: str, defined: Mapping[str, str] | None
) -> dict[str, Any]:
    """主體與適用情形由程式補上，不靠抽取者記得。"""
    # 主體只留原文寫出的行為人；其餘記成未明示（不靠提示：見 `subject`）
    answer = {**answer, "statements": normalize_subjects(statements_of(answer), text, context)[0]}
    # 上位項與句首的適用情形由程式補上，不靠抽取者記得（見 `carry`）
    labels = {str(c["code"]): str(c["label"]) for c in concepts_of(answer)} | dict(defined or {})
    carried, new_concepts = carry(statements_of(answer), text, context, labels)
    return {**answer, "statements": carried, "concepts": [*concepts_of(answer), *new_concepts]}


def _broken_ids(
    answer: Mapping[str, Any], text: str, context: str, defined: Mapping[str, str] | None
) -> dict[str, str]:
    """修復之後仍不能載入的陳述（id → 原因）。"""
    located = _located(answer, text)
    broken = unusable(located)
    broken |= dict.fromkeys(fragments(located, text), "prescriptive quote without a modal")
    broken |= criminal_penalties(located, text, context)
    for ident in shape_issues(answer, defined):
        broken.setdefault(ident, "malformed conditions")
    return broken


def _unit_statement(
    s: Mapping[str, Any],
    broken: Mapping[str, str],
    law: str,
    path: str,
    text: str,
    dropped: list[str],
) -> dict[str, Any] | None:
    """一個陳述整成要載入的樣子，並錨定在原文；要丟的回 None，原因記進 `dropped`。"""
    ident = s.get("id", "?")
    quote = str(s.get("quote", ""))
    start = locate(quote, text)
    if start < 0:
        dropped.append(f"{ident}: quote is not in the text")
        return None
    if str(ident) in broken:
        dropped.append(f"{ident}: {broken[str(ident)]} after repair")
        return None
    item = {k: v for k, v in s.items() if k != "quote"}
    if item.get("kind") != "prescriptive":
        item.pop("deontic", None)  # 只有規範性陳述有義務方向（提示規則 2）
    sanction = item.get("sanction")
    if not (isinstance(sanction, Mapping) and sanction.get("effect")):
        # 模型常把罰則欄位照樣板填成空值：沒有效果就不是罰則
        item.pop("sanction", None)
        if item.get("kind") == "sanction":
            dropped.append(f"{ident}: penalty without a sanction effect")
            return None
    elif item.get("kind") != "sanction":
        del item["sanction"]  # 罰則只屬於 sanction
    item |= {"law": law, "path": path, "text": text}
    if len(quote) != len(text):
        item["span"] = [start, start + len(quote)]
    return item


def _unit_statements(
    answer: Mapping[str, Any],
    law: str,
    path: str,
    text: str,
    context: str,
    defined: Mapping[str, str] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """可載入的陳述，與被丟掉的陳述及原因。"""
    broken = _broken_ids(answer, text, context, defined)
    statements: list[dict[str, Any]] = []
    dropped: list[str] = []
    for s in statements_of(answer):
        item = _unit_statement(s, broken, law, path, text, dropped)
        if item is not None:
            statements.append(item)
    return statements, dropped


def extract_unit(
    conn: Conn,
    model: Model,
    law: str,
    path: str,
    *,
    agent: str | None = None,
    agent_version: str,
    scheme: tuple[str, str] = ("env", "環境法概念"),
    defined: Mapping[str, str] | None = None,
    punished_by: Sequence[str] = (),
    review: Sequence[str] = (),
) -> Extracted:
    """抽取一個條文單元。`agent` 記為推導者（預設為模型的名字）；驗證者不得與它相同。

    `punished_by` 是以「違反」引用本單元的處罰條文（`link.penalized`）；
    `review` 是重抽時審查者對上一輪的意見。
    """
    provision_id, text = _unit_text(conn, law, path)
    context = ancestors_text(conn, provision_id)
    prompt = prompts.extract(
        law=law,
        path=path,
        text=text,
        context=context,
        defined=defined,
        punished_by=punished_by,
        review=review,
    )
    try:
        raw = complete(model, prompt)
    except ModelError as e:
        # 重試用盡：不寫入，下次仍是「還沒抽」
        return Extracted(None, (f"<model error: {e}>"[:300],), Counts(calls=1))
    answer: Mapping[str, Any] | None = object_from(raw)
    answer = None if answer is None else tidy(answer, defined)
    syntax_failures = 1 if answer is None else 0
    shape_failures = (
        0 if answer is None else sum(len(v) for v in shape_issues(answer, defined).values())
    )
    answer, repairs = _repair(model, prompt, raw, answer, text, context, defined, punished_by)
    counts = Counts(
        calls=1 + repairs,
        repairs=repairs,
        syntax_failures=syntax_failures,
        shape_failures=shape_failures,
    )
    if answer is None:
        return Extracted(None, ("<unparseable model output>",), counts)
    answer = _supplemented(answer, text, context, defined)
    statements, dropped = _unit_statements(answer, law, path, text, context, defined)
    statements = settled_sanctions(statements, path, text, dropped)
    base = {
        "derivation": {
            "method": "llm",
            "agent": agent or model.name,
            "agent_version": agent_version,
        },
        "review": "extracted",
        "scheme": {"code": scheme[0], "title": scheme[1]},
        "concepts": concepts_of(answer),
    }
    if not statements:
        return Extracted(None, tuple(dropped), counts)
    doc = base | {"statements": statements}
    try:
        loaded = load(conn, doc)
    except AnnotationError as e:
        # 違反約束的文件整份不寫（`load` 已回滾），記下來，不拖垮管線
        return Extracted(None, (*dropped, f"<invalid document: {e}>"), counts)
    return Extracted(loaded, tuple(dropped), counts)
