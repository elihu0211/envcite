"""規範關係「授權」的規則抽取：命令首條「本辦法依某法第某條規定訂定之」。

純規則、不用模型（`derivation.method` 為 `rule`）。法名只對語料內已有的法規名稱（任一版本的
標題）：對不到、對到數部、或對到法規而沒有該條文單元，法律端留空，不猜。款記到所屬的項。

抽取冪等：同一處引文（命令端錨點、法名、條文、對到的結果）已有主張者不再寫，駁回的也不重寫
（駁回是人的決定，不因下次同步而復活）。改了解析規則要遞增 `RULE_VERSION`。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import cast

import psycopg

from envcite_ontology.ingest.numerals import CN_NUMERALS, parse_int

log = logging.getLogger(__name__)

Conn = psycopg.Connection[tuple[object, ...]]

RULE_VERSION = "1"
PRODUCER = "envcite-delegation-rule"

_N = f"[{CN_NUMERALS}]+"
#: 「依」到「訂定」之間的引文；「之規定」「規定」「授權」是收尾詞，不屬引文。
_CLAUSE = re.compile(r"依([^。；，]*?)(?:之)?(?:規定)?(?:授權)?訂定")
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_ARTICLE = re.compile(rf"第({_N})條(?:之({_N}))?")
_UNIT = re.compile(rf"第({_N})([項款])")
_SEPARATORS = "、及和與，,"
#: 條號之後修飾條文的詞，不屬法名也不是項、款
_QUALIFIERS = re.compile(r"前段|後段|本文|但書")
#: 命令首條；引文所在的單元路徑
COMMAND_PATH = "art_1"
#: 授權者是法律或上位命令；憲法與法律自己不是被授權的命令
_NOT_COMMANDS = ("constitution", "statute")


@dataclass(frozen=True)
class Citation:
    """命令首條所引的一處授權條文：法名照原文，條文路徑已正規化（`art_7-1/para_2`）。"""

    law_name: str
    path: str


def parse(text: str) -> list[Citation]:
    """從命令首條的文字取出所有授權引文；沒有「依…訂定」或引不出條號則為空。"""
    # 舊法規的原文逐行折行、數字夾空白、「〇」寫成「○」；先正規化
    text = re.sub(r"\s+", "", text).replace("○", "〇")
    m = _CLAUSE.search(text)
    if m is None:
        return []
    cite = _QUALIFIERS.sub("", _PAREN.sub("", m.group(1)))
    cite = cite.removeprefix("據")
    found: list[Citation] = []
    law = ""
    pos = 0
    while pos < len(cite):
        if cite[pos] in _SEPARATORS:
            pos += 1
            continue
        art = _ARTICLE.match(cite, pos)
        if art is None:
            nxt = _ARTICLE.search(cite, pos)
            if nxt is None:
                break
            law = cite[pos : nxt.start()]
            pos = nxt.start()
            continue
        if not law:
            return []
        pos = art.end()
        try:
            number = str(parse_int(art.group(1)))
            if art.group(2):
                number += f"-{parse_int(art.group(2))}"
        except ValueError:
            return []
        # 條之後的項、款：「第二項及第三項」逐項成一處引文；款不單獨成條文，記到所屬的項
        paragraphs: list[str] = []
        while True:
            skip = pos
            while skip < len(cite) and cite[skip] in "、及":
                skip += 1
            unit = _UNIT.match(cite, skip)
            if unit is None:
                break
            pos = unit.end()
            if unit.group(2) == "項":
                try:
                    paragraphs.append(str(parse_int(unit.group(1))))
                except ValueError:
                    return []
        base = f"art_{number}"
        for path in [f"{base}/para_{p}" for p in paragraphs] or [base]:
            c = Citation(law, path)
            if c not in found:
                found.append(c)
    return found


@dataclass(frozen=True)
class Extracted:
    written: int
    unresolved: int
    skipped: int


def extract(conn: Conn) -> Extracted:
    """從語料內所有命令的首條抽取授權，寫成 `annotation.delegation`（規則推導、待用）。"""
    titles = _titles(conn)
    existing = _existing(conn)
    commands = conn.execute(
        """
        SELECT p.resource_id, p.id, v.content_sha256, corpus.article_text(v.id)
          FROM corpus.provision AS p
          JOIN corpus.legal_resource AS r ON r.id = p.resource_id
          JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL
         WHERE p.path = %s AND r.norm_kind <> ALL (%s::corpus.norm_kind[])
        """,
        (COMMAND_PATH, list(_NOT_COMMANDS)),
    ).fetchall()

    wanted: list[tuple[int, int, bytes, Citation, int | None]] = []
    seen: set[tuple[int, int, bytes, Citation]] = set()
    for resource_id, provision_id, sha, text in commands:
        assert isinstance(resource_id, int) and isinstance(provision_id, int)
        assert isinstance(sha, bytes)
        for c in parse(str(text or "")):
            key = (resource_id, provision_id, sha, c)
            if key in seen:
                continue
            seen.add(key)
            law = titles.get(c.law_name)
            wanted.append((resource_id, provision_id, sha, c, law))
    paths = _provisions(conn, {(law, c.path) for _, _, _, c, law in wanted if law is not None})

    derivation: int | None = None
    written = unresolved = skipped = 0
    for resource_id, provision_id, sha, c, law in wanted:
        law_provision = paths.get((law, c.path)) if law is not None else None
        row = (resource_id, provision_id, sha, c.law_name, c.path, law, law_provision)
        if row in existing:
            skipped += 1
            continue
        if derivation is None:
            derivation = _one(
                conn,
                "INSERT INTO annotation.derivation (method, producer, producer_version)"
                " VALUES ('rule', %s, %s) RETURNING id",
                PRODUCER,
                RULE_VERSION,
            )
        assertion = _one(
            conn,
            "INSERT INTO annotation.assertion (assertion_kind, derivation_id)"
            " VALUES ('delegation', %s) RETURNING id",
            derivation,
        )
        conn.execute(
            "INSERT INTO annotation.delegation"
            " (id, command_resource_id, command_provision_id, command_content_sha256,"
            "  cited_law_name, cited_path, law_resource_id, law_provision_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (assertion, *row),
        )
        _one(conn, "SELECT annotation.seal(%s)", assertion)
        existing.add(row)
        written += 1
        if law_provision is None:
            unresolved += 1
    log.info(
        "delegation: %d written (%d unresolved), %d already present", written, unresolved, skipped
    )
    return Extracted(written, unresolved, skipped)


def _titles(conn: Conn) -> dict[str, int]:
    """法規名稱（任一版本的標題）→ 法規；同名對到數部的不收，法律端因而留空。"""
    by_title: dict[str, set[int]] = {}
    for title, resource_id in conn.execute(
        "SELECT DISTINCT title, resource_id FROM corpus.legal_expression"
    ):
        by_title.setdefault(str(title), set()).add(int(str(resource_id)))
    return {t: next(iter(ids)) for t, ids in by_title.items() if len(ids) == 1}


def _provisions(
    conn: Conn, wanted: set[tuple[int | None, str]]
) -> dict[tuple[int | None, str], int]:
    pairs = sorted((r, p) for r, p in wanted if r is not None)
    if not pairs:
        return {}
    rows = conn.execute(
        "SELECT resource_id, path, id FROM corpus.provision"
        " WHERE (resource_id, path) IN (SELECT * FROM unnest(%s::bigint[], %s::text[]))",
        ([r for r, _ in pairs], [p for _, p in pairs]),
    ).fetchall()
    return {(int(str(r)), str(p)): int(str(i)) for r, p, i in rows}


def _existing(conn: Conn) -> set[tuple[int, int, bytes, str, str, int | None, int | None]]:
    """已有主張的引文（含駁回的），鍵同寫入的欄位。"""
    return {
        (int(str(r)), int(str(p)), cast(bytes, s), str(n), str(c), _opt(lr), _opt(lp))
        for r, p, s, n, c, lr, lp in conn.execute(
            "SELECT command_resource_id, command_provision_id, command_content_sha256,"
            " cited_law_name, cited_path, law_resource_id, law_provision_id"
            " FROM annotation.delegation WHERE command_provision_id IS NOT NULL"
        )
    }


def _opt(v: object) -> int | None:
    return None if v is None else int(str(v))


def _one(conn: Conn, sql: str, *args: object) -> int:
    row = conn.execute(sql, args).fetchone()
    assert row is not None
    return int(str(row[0]))
