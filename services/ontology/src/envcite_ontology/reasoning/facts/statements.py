"""某日有效、已確認的陳述及其要件、但書、優先與罰則，寫成推論程式的 ASP 事實。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.facts.asp import q
from envcite_ontology.reasoning.facts.chains import Chain, command_chains
from envcite_ontology.reasoning.facts.condition_facts import condition_facts
from envcite_ontology.reasoning.facts.relation_facts import (
    fetch_sanctions,
    relation_facts,
    sanction_lines,
    slot_facts,
    trigger_facts,
)
from envcite_ontology.reasoning.facts.vocabulary_facts import vocabulary_facts

_STATEMENTS = """
    SELECT s.id, s.statement_kind::text, s.norm_modality::text, corpus.version_iri(r.version_id),
           lr.id
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.statement AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.{resolver}(lr.id, %(lang)s, %(on)s) OFFSET 0) AS r
      JOIN annotation.statement AS s ON s.id = r.statement_id
     ORDER BY s.id
"""


# 該日因違憲失效而被排除的陳述；與 `_STATEMENTS` 同樣只對有陳述的法規解析。
_INVALIDATED = """
    SELECT i.statement_id, corpus.version_iri(i.version_id), i.ruling_no, i.invalid_from, i.partial
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.statement AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.invalidated_statements_at(lr.id, %(lang)s, %(on)s)
                         OFFSET 0) AS i
     ORDER BY 1, 4, 3
"""


class _StatementRow(NamedTuple):
    id: int
    kind: str
    modality: str | None
    version_iri: str
    law_id: int


@dataclass(frozen=True)
class Invalidated:
    """因違憲失效而不參與推論的陳述：所讀的版本、裁判字號、失效日、是否只宣告一部分。

    欄位次序同 `_INVALIDATED`，它直接是該查詢的列型別。
    """

    statement_id: int
    version_iri: str
    ruling_no: str
    invalid_from: date
    partial: bool


@dataclass(frozen=True)
class StatementFacts:
    """陳述寫成的 ASP 事實，與推論結果旁要附的資料。"""

    program: str
    #: 該日因違憲失效而被排除的陳述
    invalidated: tuple[Invalidated, ...] = ()
    #: 命令陳述 → 其命令的授權鏈（法律、憲法的陳述不在此）
    chains: Mapping[int, Chain] = field(default_factory=dict)
    #: 罰則陳述 → 法定最高額（罰鍰以外或沒有最高額者不在此）
    fine_caps: Mapping[int, int] = field(default_factory=dict)


def statement_facts(
    conn: Conn, as_of: date, language: str = "zho", *, include_unsettled: bool = False
) -> StatementFacts:
    """某日有效、已確認（或規則推導）的陳述及其要件、但書、優先、罰則，寫成 ASP 事實。

    違憲失效的陳述不在其中，另列在 `invalidated`；命令陳述的授權鏈不完整者寫成 `unrooted`。
    `include_unsettled` 僅供驗證閘門（接受前的可執行性檢查）納入未接受的陳述，此時不讀失效與授權。
    """
    params = {"lang": language, "on": as_of}
    resolver = "anchored_statements_at" if include_unsettled else "statement_versions_at"
    statements = fetch(conn, _STATEMENTS.format(resolver=resolver), params, _StatementRow)
    out = _statement_lines(statements)
    invalidated: tuple[Invalidated, ...] = ()
    chains: dict[int, Chain] = {}
    if not include_unsettled:
        invalidated = tuple(fetch(conn, _INVALIDATED, params, Invalidated))
        by_law = command_chains(conn, {stmt.law_id for stmt in statements}, language, as_of)
        chains = {stmt.id: by_law[stmt.law_id] for stmt in statements if stmt.law_id in by_law}
        out += [
            f"unrooted({stmt.id})."
            for stmt in statements
            if stmt.id in chains and not chains[stmt.id].rooted
        ]
    if not statements:
        return StatementFacts("\n".join(out), invalidated, chains)
    ids_param = {"ids": [stmt.id for stmt in statements]}

    out += condition_facts(conn, ids_param, language, as_of)
    out += vocabulary_facts(conn, params, ids_param)
    out += slot_facts(conn, ids_param)
    out += relation_facts(conn, ids_param)
    sanctions = fetch_sanctions(conn, ids_param)
    out += sanction_lines(sanctions)
    out += trigger_facts(conn, ids_param)
    caps = {
        sanc.statement_id: int(sanc.max_amount)
        for sanc in sanctions
        if sanc.effect == "fine" and sanc.max_amount is not None
    }
    return StatementFacts("\n".join(out), invalidated, chains, caps)


def _statement_lines(statements: Sequence[_StatementRow]) -> list[str]:
    out: list[str] = []
    for stmt in statements:
        out.append(f"statement({stmt.id},{stmt.kind},{stmt.modality or 'none'}).")
        out.append(f"src({stmt.id},{q(stmt.version_iri)}).")
        out.append(f"stmt_law({stmt.id},{stmt.law_id}).")
    return out
