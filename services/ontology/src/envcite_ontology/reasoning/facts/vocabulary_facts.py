"""用語定義的成員、等同的概念，與跨法受控詞彙的階層關係。"""

from collections.abc import Mapping
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch
from envcite_ontology.reasoning.facts.asp import q

_DEFINITIONS = """
    SELECT lr.id, tk.code, mk.code, t.id
      FROM (SELECT DISTINCT p.resource_id AS id
              FROM annotation.term_definition AS x
              JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
     CROSS JOIN LATERAL (SELECT * FROM annotation.definitions_at(lr.id, %(lang)s, %(on)s)
                         OFFSET 0) AS d
      JOIN annotation.term_definition AS t ON t.id = d.definition_id
      JOIN vocab.concept AS tk ON tk.id = t.term_concept_id
      JOIN annotation.term_definition_member AS m ON m.definition_id = t.id
      JOIN vocab.concept AS mk ON mk.id = m.member_concept_id
     ORDER BY lr.id, tk.code, mk.code
"""


class _DefinitionRow(NamedTuple):
    law_id: int
    term: str
    member: str
    definition_id: int


# 對到同一個外部識別碼的概念視為同一個（只記等同）：要件、概念值與定義成員所用的概念，
# 和與它等同的其他概念之代碼。推論把後者的事實當成前者的事實。
_MATCHES = """
    SELECT DISTINCT other.code, used.code
      FROM vocab.concept AS used
      JOIN vocab.concept_match AS m ON m.concept_id = used.id
      JOIN vocab.concept_match AS om ON om.external_system = m.external_system
                                    AND om.external_id = m.external_id
                                    AND om.concept_id <> m.concept_id
      JOIN vocab.concept AS other ON other.id = om.concept_id
     WHERE other.code <> used.code
       AND used.id IN (SELECT c.concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT c.value_concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT c.delegate_permit_concept_id FROM annotation.statement_condition AS c
                        WHERE c.statement_id = ANY(%(ids)s)
                       UNION
                       SELECT m.member_concept_id FROM annotation.term_definition_member AS m
                        WHERE m.definition_id = ANY(%(definitions)s::bigint[]))
     ORDER BY 1, 2
"""


class _MatchRow(NamedTuple):
    other: str
    used: str


# 跨法受控詞彙上位／等同關係（vocab.concept_relation）。
_CONCEPT_RELATIONS = """
    SELECT src.code, tgt.code, r.relation_kind::text
      FROM vocab.concept_relation AS r
      JOIN vocab.concept AS src ON src.id = r.source_concept_id
      JOIN vocab.concept AS tgt ON tgt.id = r.target_concept_id
     ORDER BY src.code, tgt.code
"""


class _ConceptRelRow(NamedTuple):
    source: str
    target: str
    kind: str


def vocabulary_facts(
    conn: Conn, params: Mapping[str, object], ids_param: Mapping[str, list[int]]
) -> list[str]:
    """用語定義的成員、等同的概念與跨法受控詞彙階層關係。"""
    out: list[str] = []
    # 用語定義只及於本法：成員關係帶法規編號，推論只在要件所屬的法規內使用。
    definitions = fetch(conn, _DEFINITIONS, params, _DefinitionRow)
    for defn in definitions:
        out.append(f"defmember({defn.law_id},{q(defn.term)},{q(defn.member)}).")
        out.append(f"defsrc({defn.law_id},{q(defn.term)},{q(defn.member)},{defn.definition_id}).")
    definition_ids = sorted({defn.definition_id for defn in definitions})
    matches = fetch(conn, _MATCHES, {**ids_param, "definitions": definition_ids}, _MatchRow)
    out += [f"same({q(match.other)},{q(match.used)})." for match in matches]
    for rel in fetch(conn, _CONCEPT_RELATIONS, (), _ConceptRelRow):
        pred = "same" if rel.kind == "exact_match" else "concept_broader"
        out.append(f"{pred}({q(rel.source)},{q(rel.target)}).")
    return out
