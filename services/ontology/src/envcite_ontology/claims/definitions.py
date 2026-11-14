"""用語定義的寫入：錨在定義條文上，成員概念是該定義明文涵蓋的用語。"""

from collections.abc import Mapping, Sequence

from envcite_ontology.annotation_document import (
    Definition,
)
from envcite_ontology.claims.anchoring import anchor
from envcite_ontology.claims.errors import AnnotationError
from envcite_ontology.claims.rows import new_claim, seal, span_range
from envcite_ontology.claims.vocabulary import resolve
from envcite_ontology.db import Conn


def write_definitions(
    conn: Conn,
    definitions: Sequence[Definition],
    concepts: Mapping[str, int],
    derivation_id: int,
    review: str,
) -> tuple[int, int]:
    """用語定義：錨在定義條文上；成員概念是該定義明文涵蓋的用語。回傳（定義數，沿用既有者數）。"""
    seen: set[str] = set()
    repeated = 0
    for t in definitions:
        if t.id in seen:
            raise AnnotationError(f"用語定義 id 重複：{t.id}")
        seen.add(t.id)
        provision_id, digest = anchor(conn, "用語定義", t.id, t.law, t.path, t.text)
        did = new_claim(conn, "definition", derivation_id, review)
        conn.execute(
            "INSERT INTO annotation.term_definition"
            " (id, provision_id, content_sha256, span, term_concept_id, definition_text)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                did,
                provision_id,
                digest,
                span_range(t.span),
                resolve(concepts, t.term),
                t.definition,
            ),
        )
        for member in t.members:
            conn.execute(
                "INSERT INTO annotation.term_definition_member (definition_id, member_concept_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (did, resolve(concepts, member)),
            )
        if seal(conn, did) != did:
            repeated += 1
    return len(seen), repeated
