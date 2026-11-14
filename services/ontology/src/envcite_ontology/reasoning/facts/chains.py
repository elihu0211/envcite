"""命令的授權鏈與委由授權條文的限值：沿 `delegations_at` 往上走到法律；解析在 SQL，這裡不重算。"""

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch, fetch_one

_LAWS = """
    SELECT id, local_id, norm_kind::text FROM corpus.legal_resource WHERE id = ANY(%s)
"""


#: 不是被授權的命令（與 `delegation._NOT_COMMANDS` 同義）：授權鏈到此為止。
LAWS = ("constitution", "statute")


class _LawRow(NamedTuple):
    id: int
    local_id: str
    norm_kind: str


class _ResourceRow(NamedTuple):
    jurisdiction: str
    id_scheme: str
    local_id: str


# 命令在該日的每一處授權：對到的法規、條文該日的狀態、位階，以及該版本是否已違憲失效。
_LINKS = """
    SELECT d.cited_law_name, d.cited_path, d.law_resource_id, d.law_local_id, d.law_provision_id,
           d.law_status::text, lr.norm_kind::text, corpus.version_iri(d.law_version_id),
           EXISTS (SELECT 1 FROM corpus.invalidations_at(d.law_resource_id, %(lang)s, %(on)s) AS i
                    WHERE i.version_id = d.law_version_id),
           lr.jurisdiction, lr.id_scheme
      FROM annotation.delegations_at(%(law)s, %(lang)s, %(on)s) AS d
      LEFT JOIN corpus.legal_resource AS lr ON lr.id = d.law_resource_id
     ORDER BY d.cited_path, d.delegation_id
"""


class _LinkRow(NamedTuple):
    cited_law_name: str
    cited_path: str
    # 引不到法規（或條文）時以下各欄為 NULL：LEFT JOIN 與 `delegations_at` 的外連接
    law_id: int | None
    law_local_id: str | None
    law_provision_id: int | None
    law_status: str | None
    law_norm_kind: str | None
    version_iri: str | None
    version_invalidated: bool
    law_jurisdiction: str | None
    law_id_scheme: str | None


@dataclass(frozen=True)
class Link:
    """授權鏈的一環：命令所引的授權條文；`gap` 為 None 即這一環成立。

    `gap`：`no_citation`、`unresolved_law`、`law_not_in_force`、`law_invalidated`、`cycle`。
    """

    command_jurisdiction: str
    command_id_scheme: str
    command_local_id: str
    cited_law_name: str
    cited_path: str
    target_jurisdiction: str | None
    target_id_scheme: str | None
    target_local_id: str | None
    version_iri: str | None
    gap: str | None


@dataclass(frozen=True)
class Chain:
    """命令的授權鏈：`rooted` 是至少一條路徑經成立的環節到達法律。"""

    rooted: bool
    links: tuple[Link, ...]


def chain_at(conn: Conn, law: int, language: str, on: date) -> Chain:
    """命令在該日的授權鏈。授權者又是命令時接著往上走；回到走過的命令即 `cycle`。"""
    return _chain(conn, law, language, on, ())


def _chain(
    conn: Conn,
    law: int,
    language: str,
    on: date,
    path: tuple[int, ...],
) -> Chain:
    # 不跨起點快取：在環上被截斷的鏈只對那個起點成立。
    command = fetch_one(
        conn,
        "SELECT jurisdiction, id_scheme, local_id FROM corpus.legal_resource WHERE id = %s",
        (law,),
        _ResourceRow,
    )
    assert command is not None
    rows = fetch(conn, _LINKS, {"law": law, "lang": language, "on": on}, _LinkRow)
    if not rows:
        return Chain(
            False,
            (
                Link(
                    command_jurisdiction=command.jurisdiction,
                    command_id_scheme=command.id_scheme,
                    command_local_id=command.local_id,
                    cited_law_name="",
                    cited_path="",
                    target_jurisdiction=None,
                    target_id_scheme=None,
                    target_local_id=None,
                    version_iri=None,
                    gap="no_citation",
                ),
            ),
        )
    links: list[Link] = []
    rooted = False
    for row in rows:
        here = Link(
            command_jurisdiction=command.jurisdiction,
            command_id_scheme=command.id_scheme,
            command_local_id=command.local_id,
            cited_law_name=row.cited_law_name,
            cited_path=row.cited_path,
            target_jurisdiction=row.law_jurisdiction,
            target_id_scheme=row.law_id_scheme,
            target_local_id=row.law_local_id,
            version_iri=row.version_iri,
            gap=None,
        )
        if row.law_id is None or row.law_provision_id is None:
            links.append(_gap(here, "unresolved_law"))
        elif row.law_status != "in_force":
            links.append(_gap(here, "law_not_in_force"))
        elif row.version_invalidated:
            links.append(_gap(here, "law_invalidated"))
        elif row.law_norm_kind in LAWS:
            links.append(here)
            rooted = True
        elif row.law_id in path or row.law_id == law:
            links.append(_gap(here, "cycle"))
        else:
            links.append(here)
            above = _chain(conn, row.law_id, language, on, (*path, law))
            links.extend(above.links)
            rooted = rooted or above.rooted
    return Chain(rooted, tuple(links))


def _gap(link: Link, gap: str) -> Link:
    return replace(link, version_iri=None, gap=gap)


# 授權的限值與它的認定方式（限值那一列要件上的檢測方法、平均時段）。
_LIMIT = """
    SELECT s.threshold, s.statement_id, mk.code, ak.code
      FROM annotation.threshold_source_at(%s, %s, %s) AS s
      JOIN annotation.statement_condition AS lim ON lim.id = s.condition_id
      LEFT JOIN vocab.concept AS mk ON mk.id = lim.measurement_method_concept_id
      LEFT JOIN vocab.concept AS ak ON ak.id = lim.averaging_period_concept_id
     ORDER BY s.condition_id
"""


@dataclass(frozen=True)
class Limit:
    """委由授權條文的要件在某日所取得的限值：門檻值、提供它的陳述與它的認定方式（概念代碼）。

    欄位次序同 `_LIMIT`，它直接是該查詢的列型別。
    """

    threshold: Decimal
    statement_id: int
    method: str | None
    averaging: str | None


def limit_at(conn: Conn, condition_id: int, language: str, on: date) -> Limit | None:
    """授權的限值；解析不到為 None。同一條文對同一概念有數個限值時取要件編號最小的。"""
    return fetch_one(conn, _LIMIT, (condition_id, language, on), Limit)


# 多維適用條件之所有已確認限值候選（批次查詢，避免 N+1 round-trip）。
_LIMITS_BATCH = """
    SELECT c.id, s.threshold, s.statement_id, s.measurement_method, s.averaging_period
      FROM unnest(%(ids)s::bigint[]) AS c(id)
     CROSS JOIN LATERAL annotation.threshold_sources_at(c.id, %(lang)s, %(on)s) AS s
     ORDER BY c.id, s.statement_id, s.condition_id
"""


# 委由授權條文的限值，條文版本在該日已違憲失效（threshold_source_at 因而解析不到）。
_VOID_LIMITS_BATCH = """
    SELECT c.id
      FROM annotation.statement_condition AS c
      JOIN corpus.provision AS dp ON dp.id = c.delegate_provision_id
     WHERE c.id = ANY(%(ids)s::bigint[])
       AND EXISTS (
           SELECT 1
             FROM corpus.invalidations_at(dp.resource_id, %(lang)s, %(on)s) AS i
            WHERE i.provision_id = dp.id
       )
"""


@dataclass(frozen=True)
class DelegatedLimitRow:
    """授權條文所載的一個限值候選：門檻、哪個陳述、檢測方法與平均時段。"""

    condition_id: int
    threshold: Decimal
    statement_id: int
    method: str | None
    averaging: str | None


@dataclass(frozen=True)
class VoidLimitRow:
    """委由授權條文的要件，條文版本在該日已違憲失效、所以解析不到限值。"""

    condition_id: int


def batch_delegated_limits(
    conn: Conn, delegated_ids: list[int], language: str, on: date
) -> tuple[dict[int, list[DelegatedLimitRow]], set[int]]:
    """一批委由授權條文的要件各自解析到的限值，與解析不到而限值失效的要件編號。"""
    if not delegated_ids:
        return {}, set()
    limits_by_cond: dict[int, list[DelegatedLimitRow]] = {}
    for row in fetch(
        conn,
        _LIMITS_BATCH,
        {"ids": delegated_ids, "lang": language, "on": on},
        DelegatedLimitRow,
    ):
        limits_by_cond.setdefault(row.condition_id, []).append(row)
    missing_ids = [cid for cid in delegated_ids if cid not in limits_by_cond]
    if not missing_ids:
        return limits_by_cond, set()
    void_rows = fetch(
        conn,
        _VOID_LIMITS_BATCH,
        {"ids": missing_ids, "lang": language, "on": on},
        VoidLimitRow,
    )
    return limits_by_cond, {row.condition_id for row in void_rows}


def command_chains(
    conn: Conn, law_ids: Collection[int], language: str, on: date
) -> dict[int, Chain]:
    """命令的法規 → 它的授權鏈；法律、憲法不在其中。"""
    commands = [
        row.id
        for row in fetch(conn, _LAWS, (sorted(law_ids),), _LawRow)
        if row.norm_kind not in LAWS
    ]
    return {law: chain_at(conn, law, language, on) for law in commands}
