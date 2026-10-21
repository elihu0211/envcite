from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from envcite_ontology.migrate import MIGRATIONS_DIR, MigrationChanged, apply_all


def test_a_database_from_another_baseline_is_refused(dsn: str) -> None:
    """Applying the current files over a retired baseline would leave both schemas."""
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO schema_migration (filename, sha256) VALUES ('0001_retired.sql', 'x')"
        )
        try:
            with pytest.raises(MigrationChanged, match=r"0001_retired\.sql"):
                apply_all(conn)
        finally:
            conn.rollback()


def test_a_database_at_the_first_baseline_upgrades_to_the_current_files(
    dsn: str, tmp_path: Path
) -> None:
    """An existing database gets only the new files, and a new enum value must be
    committed before a later file uses it — which a fresh database, where the type is
    created in the same transaction, never exercises."""
    name = "upgrade_path"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name >= "0005":
                break
            (baseline / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        with psycopg.connect(make_conninfo(dsn, dbname=name)) as conn:
            assert apply_all(conn, baseline)[-1].startswith("0004")
            upgraded = apply_all(conn)
            assert upgraded[0].startswith("0005") and len(upgraded) >= 3
            conn.execute(
                "INSERT INTO corpus.legal_resource (id_scheme, local_id, norm_kind, document_type)"
                " VALUES ('moj_pcode', 'T0000001', 'statute', '法律')"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO corpus.lifecycle_event (resource_id, event_kind, event_date)"
                    " SELECT id, 'unknown', NULL FROM corpus.legal_resource"
                )
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _at(dsn: str, name: str, tmp_path: Path, before: str) -> psycopg.Connection[tuple[object, ...]]:
    """A fresh database with the migrations before `before` applied."""
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    baseline = tmp_path / name
    baseline.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name >= before:
            break
        (baseline / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    conn = psycopg.connect(make_conninfo(dsn, dbname=name))
    apply_all(conn, baseline)
    return conn


def _drop(dsn: str, name: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


_BEFORE_CLAIM = """
INSERT INTO corpus.legal_resource (id, id_scheme, local_id, norm_kind, document_type)
OVERRIDING SYSTEM VALUE VALUES (1, 'moj_pcode', 'T0000001', 'statute', '法律');
INSERT INTO corpus.legal_expression
    (id, resource_id, language, version_date, title, text_period, text_status, text_authority)
OVERRIDING SYSTEM VALUE
VALUES (1, 1, 'zho', '2020-01-01', '測試法', '[2020-01-01,)', 'available', 'official_db');
INSERT INTO corpus.provision (id, resource_id, provision_type, path)
OVERRIDING SYSTEM VALUE VALUES (1, 1, 'article', 'art_1');
INSERT INTO corpus.provision_version
    (provision_id, expression_id, resource_id, content, force_period, commencement_rule)
VALUES (1, 1, 1, '甲一', '[2020-01-01,)', 'specified');
INSERT INTO annotation.derivation (id, method, producer, producer_version)
OVERRIDING SYSTEM VALUE VALUES (1, 'llm', 'model-a', '1');
INSERT INTO annotation.statement
    (id, provision_id, content_sha256, statement_kind, norm_modality, derivation_id, confirm_state)
OVERRIDING SYSTEM VALUE
VALUES (1, 1, sha256('甲一'::bytea), 'constitutive', NULL, 1, 'confirmed'),
       (2, 1, sha256('甲一'::bytea), 'prescriptive', 'obligation', 1, 'extracted');
INSERT INTO vocab.concept_scheme (id, code, title, scope_resource_id)
OVERRIDING SYSTEM VALUE VALUES (1, 'def.T0000001', '本法用語', 1);
INSERT INTO vocab.concept (id, scheme_id, code, name)
OVERRIDING SYSTEM VALUE VALUES (1, 1, 'business', '事業'), (2, 1, 'factory', '工廠');
INSERT INTO annotation.term_definition (id, provision_id, content_sha256, term_concept_id,
                                       definition_text, derivation_id, confirm_state)
OVERRIDING SYSTEM VALUE
VALUES (4, 1, sha256('甲一'::bytea), 1, '指工廠', 1, 'extracted'),
       (5, 1, sha256('甲一'::bytea), 2, '指工廠', 1, 'rejected');
INSERT INTO annotation.term_definition_member (definition_id, member_concept_id) VALUES (4, 2);
SELECT annotation.record_verification(2, 'a', 'tool', 'pass');
SELECT annotation.record_definition_verification(5, 'literal', 'tool', 'fail');
"""


def test_statements_and_definitions_become_claims_with_their_records(
    dsn: str, tmp_path: Path
) -> None:
    """陳述保留 id；定義改發的 id 與舊 id 範圍重疊（舊 4、5 → 新 3、4），成員與驗證紀錄跟著走。"""
    name = "claim_upgrade"
    try:
        with _at(dsn, name, tmp_path, "0026") as conn:
            conn.execute(_BEFORE_CLAIM)
            conn.commit()
            assert apply_all(conn)[0] == "0026_claim.sql"

            claims = conn.execute(
                "SELECT id, assertion_kind::text, confirm_state::text"
                " FROM annotation.assertion ORDER BY id"
            ).fetchall()
            assert claims == [
                (1, "statement", "confirmed"),
                (2, "statement", "extracted"),
                (3, "definition", "extracted"),
                (4, "definition", "rejected"),
            ]
            assert conn.execute(
                "SELECT id, term_concept_id FROM annotation.term_definition ORDER BY id"
            ).fetchall() == [(3, 1), (4, 2)]
            assert conn.execute(
                "SELECT definition_id FROM annotation.term_definition_member"
            ).fetchall() == [(3,)]
            records = conn.execute(
                "SELECT assertion_id, method, result::text FROM annotation.verification"
                " WHERE method <> 'policy' ORDER BY id"
            ).fetchall()
            assert records == [(2, "a", "pass"), (4, "literal", "fail")]

            conn.execute("DELETE FROM corpus.provision")
            assert conn.execute("SELECT count(*) FROM annotation.assertion").fetchone() == (0,)
            conn.rollback()
    finally:
        _drop(dsn, name)


_DUPLICATES = """
INSERT INTO corpus.legal_resource (id, id_scheme, local_id, norm_kind, document_type)
OVERRIDING SYSTEM VALUE VALUES (1, 'moj_pcode', 'T0000001', 'statute', '法律');
INSERT INTO corpus.legal_expression
    (id, resource_id, language, version_date, title, text_period, text_status, text_authority)
OVERRIDING SYSTEM VALUE
VALUES (1, 1, 'zho', '2020-01-01', '測試法', '[2020-01-01,)', 'available', 'official_db');
INSERT INTO corpus.provision (id, resource_id, provision_type, path)
OVERRIDING SYSTEM VALUE VALUES (1, 1, 'article', 'art_1');
INSERT INTO corpus.provision_version
    (provision_id, expression_id, resource_id, content, force_period, commencement_rule)
VALUES (1, 1, 1, '甲一', '[2020-01-01,)', 'specified');
INSERT INTO annotation.derivation (id, method, producer, producer_version)
OVERRIDING SYSTEM VALUE VALUES (1, 'llm', 'model-a', '1');
INSERT INTO annotation.claim (id, claim_kind, derivation_id, confirm_state)
OVERRIDING SYSTEM VALUE
VALUES (1, 'statement', 1, 'confirmed'), (2, 'statement', 1, 'extracted'),
       (3, 'statement', 1, 'rejected');
INSERT INTO annotation.statement (id, provision_id, content_sha256, statement_kind)
VALUES (1, 1, sha256('甲一'::bytea), 'constitutive'), (2, 1, sha256('甲一'::bytea), 'constitutive'),
       (3, 1, sha256('甲一'::bytea), 'constitutive');
"""


def test_existing_duplicates_keep_the_earliest_and_reject_the_rest(
    dsn: str, tmp_path: Path
) -> None:
    name = "seal_upgrade"
    try:
        with _at(dsn, name, tmp_path, "0028") as conn:
            conn.execute(_DUPLICATES)
            conn.commit()
            assert apply_all(conn)[0] == "0028_claim_shape.sql"

            assert conn.execute(
                "SELECT id, confirm_state::text, fingerprint IS NOT NULL"
                " FROM annotation.assertion ORDER BY id"
            ).fetchall() == [(1, "confirmed", True), (2, "rejected", True), (3, "rejected", True)]
            assert conn.execute(
                "SELECT assertion_id, verifier, evidence FROM annotation.verification"
                " WHERE method = 'policy' AND assertion_id = 2"
            ).fetchall() == [
                (2, "migration:0028", {"action": "rejected", "reason": "duplicate", "kept": 1})
            ]
    finally:
        _drop(dsn, name)


def test_the_lexical_index_becomes_one_row_per_passage(dsn: str, tmp_path: Path) -> None:
    """0032：既有的字面索引保留；同一段文字若有並存的分詞器列，併成一列（下次重建就地更正）。"""
    name = "passage_upgrade"
    try:
        with _at(dsn, name, tmp_path, "0032") as conn:
            conn.execute(
                "INSERT INTO retrieval.lexical_index (content_sha256, tokenizer, tsv) VALUES"
                " (sha256('甲'::bytea), 'tok-a', to_tsvector('simple', '甲')),"
                " (sha256('甲'::bytea), 'tok-b', to_tsvector('simple', '甲')),"
                " (sha256('乙'::bytea), 'tok-a', to_tsvector('simple', '乙'))"
            )
            conn.commit()
            assert apply_all(conn)[0] == "0032_passage.sql"

            assert conn.execute(
                "SELECT count(*), count(DISTINCT content_sha256), count(embedding)"
                " FROM retrieval.passage"
            ).fetchone() == (2, 2, 0)
    finally:
        _drop(dsn, name)
