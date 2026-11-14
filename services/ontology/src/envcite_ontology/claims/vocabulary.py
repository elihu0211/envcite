"""詞彙：概念方案、概念與外部對映的寫入，以及代碼到概念 id 的解析。"""

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from envcite_ontology.annotation_document import (
    Concept,
    Scheme,
)
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.rows import one
from envcite_ontology.db import Conn, fetch, fetch_one


class _IdRow(NamedTuple):
    id: int


class _ScopeRow(NamedTuple):
    scope_resource_id: int | None


class _ConceptRow(NamedTuple):
    code: str
    id: int


class _ExternalRow(NamedTuple):
    external_id: str


def write_concepts(
    conn: Conn, scheme: Scheme | None, concepts: Sequence[Concept]
) -> dict[str, int]:
    """該詞彙方案下文件與資料庫既有的全部概念，代碼 → id。"""
    if scheme is None:
        if concepts:
            raise AnnotationError("有概念但沒有 scheme")
        return {}
    # 方案可限定在一部法規（立法定義的用語只及於本法）；沒有 law 則是跨法共用。
    scope: int | None = None
    if scheme.law is not None:
        row = fetch_one(
            conn,
            "SELECT id FROM corpus.legal_resource WHERE id_scheme = 'moj_pcode' AND local_id = %s",
            (scheme.law,),
            _IdRow,
        )
        if row is None:
            raise AnnotationError(f"語料裡沒有法規 {scheme.law}")
        scope = row.id
    scheme_id = one(
        conn,
        "INSERT INTO vocab.concept_scheme (code, title, scope_resource_id) VALUES (%s, %s, %s)"
        " ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id",
        scheme.code,
        scheme.title,
        scope,
    )
    existing = fetch_one(
        conn,
        "SELECT scope_resource_id FROM vocab.concept_scheme WHERE id = %s",
        (scheme_id,),
        _ScopeRow,
    )
    if existing is not None and existing.scope_resource_id != scope:
        raise AnnotationError(f"詞彙方案「{scheme.code}」已存在，且適用範圍與文件不同")
    for c in concepts:
        if "broader" in c.model_fields_set:
            # 詞彙沒有上下位：「甲屬於乙」寫成用語定義（`definitions`），不寫在概念上。
            raise AnnotationError(f"概念「{c.code}」帶了 broader；上下位請寫成用語定義")
        cid = one(
            conn,
            "INSERT INTO vocab.concept (scheme_id, code, name) VALUES (%s, %s, %s)"
            " ON CONFLICT (scheme_id, code) DO UPDATE SET name = EXCLUDED.name"
            " RETURNING id",
            scheme_id,
            c.code,
            c.label,
        )
        for alt in c.aliases:
            conn.execute(
                "INSERT INTO vocab.concept_label (concept_id, label, label_kind)"
                " VALUES (%s, %s, 'alias') ON CONFLICT DO NOTHING",
                (cid, alt),
            )
        _matches(conn, cid, c.code, c.matches or {})
    return {
        r.code: r.id
        for r in fetch(
            conn,
            "SELECT code, id FROM vocab.concept WHERE scheme_id = %s",
            (scheme_id,),
            _ConceptRow,
        )
    }


def _matches(conn: Conn, concept_id: int, code: object, matches: Mapping[str, str]) -> None:
    """外部對映（外部體系 → 識別碼），只記等同；與既有的對映不同就拒絕，不覆寫。"""
    for system, external in matches.items():
        conn.execute(
            "INSERT INTO vocab.concept_match (concept_id, external_system, external_id)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (concept_id, system, external),
        )
        row = fetch_one(
            conn,
            "SELECT external_id FROM vocab.concept_match"
            " WHERE concept_id = %s AND external_system = %s",
            (concept_id, system),
            _ExternalRow,
        )
        if row is None or row.external_id != external:
            known = None if row is None else row.external_id
            raise AnnotationError(f"概念「{code}」在 {system} 已對映到 {known}，不是 {external}")


def resolve(concepts: Mapping[str, int], code: object) -> int:
    """概念代碼對 id；引用不存在的概念則拒絕。"""
    if str(code) not in concepts:
        raise AnnotationError(f"引用了不存在的概念：{code}")
    return concepts[str(code)]
