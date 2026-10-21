"""Annotation: statements anchored to text, not a version row."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.acquire.moj import Moj
from envcite_ontology.annotate import AnnotationError, load
from envcite_ontology.ingest import importer
from tests.sample import stored_snapshot
from tests.test_import import _law, _scalar
from tests.test_incremental import (
    _articles,
    _first,
    _law_id,
    _law_v1,
    _law_v2,
    _next,
)

Conn = psycopg.Connection[tuple[object, ...]]

LAW = "T0000001"
BEFORE = date(2019, 1, 1)  # v1 text in force
AFTER = date(2020, 6, 1)  # v2 text in force (art. 2 amended on 2020-03-01)


def _doc(
    *statements: dict[str, Any], review: str = "confirmed", method: str = "human"
) -> dict[str, Any]:
    return {
        "derivation": {"method": method, "agent": "test", "agent_version": "1"},
        "review": review,
        "scheme": {"code": "env", "title": "環境法概念"},
        "concepts": [
            {"code": "discharging", "label": "排放廢水"},
            {"code": "cod", "label": "化學需氧量", "aliases": ["COD"]},
        ],
        "statements": list(statements),
    }


def _duty(text: str = "甲一") -> dict[str, Any]:
    """Art. 1: dischargers must meet the limit set by art. 2 (delegated)."""
    return {
        "id": "duty",
        "law": LAW,
        "path": "art_1",
        "text": text,
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            {
                "role": "scope",
                "clause": 1,
                "concept": "discharging",
                "comparator": "eq",
                "threshold": 1,
            },
            {
                "role": "duty",
                "clause": 1,
                "concept": "cod",
                "comparator": "le",
                "delegate": {"law": LAW, "path": "art_2"},
            },
        ],
    }


def _limit(text: str, value: int) -> dict[str, Any]:
    """Art. 2: the limit itself, on the unit's own text."""
    return {
        "id": f"limit{value}",
        "law": LAW,
        "path": "art_2",
        "text": text,
        "kind": "constitutive",
        "conditions": [
            {"role": "limit", "clause": 1, "concept": "cod", "comparator": "le", "threshold": value}
        ],
    }


def _facts(conn: Conn, on: date) -> int:
    return int(
        _scalar(
            conn, "SELECT count(*) FROM annotation.statements_at(%s, 'zho', %s)", _law_id(conn), on
        )
    )


def _duty_condition(conn: Conn) -> int:
    return int(
        _scalar(conn, "SELECT id FROM annotation.statement_condition WHERE condition_role = 'duty'")
    )


def test_a_loaded_statement_is_a_fact_on_the_day_its_text_is_in_force(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_duty(), _limit("乙一", 100)))

    assert (loaded.statements, loaded.conditions) == (2, 3)
    assert _facts(conn, BEFORE) == 2


def test_a_statement_survives_the_version_row_being_replaced(conn: Conn, tmp_path: Path) -> None:
    """F4：效力列更替而文字沒變，標註不消失。錨在版本列編號上，這裡會降為一。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))

    conn.execute(
        "UPDATE corpus.provision_version SET superseded_at = now() + interval '1 second'"
        " WHERE content = '甲一'"
    )
    conn.execute(
        "INSERT INTO corpus.provision_version"
        " (provision_id, expression_id, resource_id, content, force_period, commencement_rule)"
        " SELECT provision_id, expression_id, resource_id, content, force_period, commencement_rule"
        "   FROM corpus.provision_version WHERE content = '甲一'"
    )
    conn.commit()

    assert _facts(conn, BEFORE) == 2


def test_a_statement_drops_out_when_its_text_is_corrected(conn: Conn, tmp_path: Path) -> None:
    """文字真的改了，標註不沿用到新文字：該單元的陳述須重新標註與審核。"""
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))

    corrected = _law_v1(LawArticles=_articles("甲一", "乙一更正"))
    assert _next(conn, tmp_path, corrected).actions == {"corrected": 1}

    assert _facts(conn, BEFORE) == 1  # 只剩 art. 1 的陳述


@pytest.mark.parametrize(
    ("review", "method", "expected"),
    [
        ("extracted", "human", 0),
        ("rejected", "human", 0),
        ("confirmed", "human", 2),
        ("extracted", "rule", 2),
    ],
)
def test_only_confirmed_or_rule_derived_statements_are_facts(
    conn: Conn, tmp_path: Path, review: str, method: str, expected: int
) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100), review=review, method=method))

    assert _facts(conn, BEFORE) == expected


def test_a_delegated_threshold_is_the_limit_in_force_on_the_date(
    conn: Conn, tmp_path: Path
) -> None:
    """標準修正、法律不動：同一個要件，不同日期取到不同的限值。"""
    _first(conn, tmp_path, _law_v1())
    assert _next(conn, tmp_path, _law_v2()).actions == {"versioned": 1}
    load(conn, _doc(_duty(), _limit("乙一", 100), _limit("乙二", 80)))
    cond = _duty_condition(conn)

    def threshold(on: date) -> Decimal | None:
        value = _scalar(conn, "SELECT annotation.threshold_at(%s, 'zho', %s)", cond, on)
        return None if value is None else Decimal(value)

    assert threshold(BEFORE) == Decimal(100)
    assert threshold(AFTER) == Decimal(80)
    assert threshold(date(2000, 1, 1)) is None  # 授權命令尚未施行：解析不到，不冒充任何值


def test_a_threshold_from_an_unconfirmed_limit_is_not_used(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))
    conn.execute(
        "UPDATE annotation.claim SET confirm_state = 'extracted' WHERE id IN"
        " (SELECT s.id FROM annotation.statement AS s"
        "    JOIN corpus.provision AS p ON p.id = s.provision_id WHERE p.path = 'art_2')"
    )
    conn.commit()

    value = _scalar(
        conn, "SELECT annotation.threshold_at(%s, 'zho', %s)", _duty_condition(conn), BEFORE
    )
    assert value is None


def test_exceptions_overrides_and_sanctions_are_loaded(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一", "丙一")))
    doc = _doc(
        _duty(),
        {**_limit("乙一", 100), "kind": "constitutive", "exception_of": "duty"},
        {
            "id": "penalty",
            "law": LAW,
            "path": "art_3",
            "text": "丙一",
            "kind": "sanction",
            "sanction": {"punishes": "duty", "effect": "fine", "min": 60000, "max": 20000000},
        },
    )
    doc["overrides"] = [{"winner": "limit100", "loser": "duty", "basis": "lex_specialis"}]
    loaded = load(conn, doc)

    assert (loaded.exceptions, loaded.overrides, loaded.sanctions) == (1, 1, 1)
    assert _scalar(conn, "SELECT max_amount FROM annotation.sanction") == 20000000


def test_text_that_is_not_in_the_corpus_is_refused_and_nothing_is_written(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())

    with pytest.raises(AnnotationError, match="錨定的文字"):
        load(conn, _doc(_duty(), _limit("從未存在過的條文", 100)))

    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0
    assert _scalar(conn, "SELECT count(*) FROM annotation.derivation") == 0
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept") == 0


def test_a_reference_to_an_unknown_statement_or_concept_is_refused(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    bad_exception = _doc({**_limit("乙一", 100), "exception_of": "nope"})
    with pytest.raises(AnnotationError, match="不存在的陳述"):
        load(conn, bad_exception)

    bad_concept = _doc(_duty())
    bad_concept["statements"][0]["conditions"][0]["concept"] = "nope"
    with pytest.raises(AnnotationError, match="不存在的概念"):
        load(conn, bad_concept)


@pytest.mark.parametrize(
    ("kind", "deontic", "digest"),
    [
        ("prescriptive", None, b"\x01" * 32),  # 規範性陳述要有義務方向
        ("constitutive", "obligation", b"\x01" * 32),  # 其餘不可有
        ("constitutive", None, b"\x00"),  # 雜湊長度
    ],
)
def test_statement_constraints(
    conn: Conn, tmp_path: Path, kind: str, deontic: str | None, digest: bytes
) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty()))
    provision = _scalar(conn, "SELECT provision_id FROM annotation.statement")
    claim = _scalar(
        conn,
        "INSERT INTO annotation.claim (claim_kind, derivation_id)"
        " SELECT 'statement', id FROM annotation.derivation RETURNING id",
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO annotation.statement"
            " (id, provision_id, content_sha256, statement_kind, norm_modality)"
            " VALUES (%s, %s, %s, %s, %s)",
            (claim, provision, digest, kind, deontic),
        )


def test_a_condition_has_exactly_one_threshold_source_and_a_limit_has_a_value(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty()))
    statement = _scalar(conn, "SELECT id FROM annotation.statement")
    concept = _scalar(conn, "SELECT id FROM vocab.concept WHERE code = 'cod'")
    insert = (
        "INSERT INTO annotation.statement_condition"
        " (statement_id, condition_role, disjunct_no, concept_id, comparator,"
        "  threshold, value_concept_id)"
        " VALUES (%s, 'duty', 1, %s, 'le', %s, %s)"
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(insert, (statement, concept, None, None))  # 沒有來源
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(insert, (statement, concept, 1, concept))  # 兩個來源
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO annotation.statement_condition"
            " (statement_id, condition_role, disjunct_no, concept_id, comparator, value_concept_id)"
            " VALUES (%s, 'limit', 1, %s, 'le', %s)",
            (statement, concept, concept),
        )  # 限值必須是數值
    conn.rollback()


def test_deleting_a_law_removes_its_annotations(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _doc(_duty(), _limit("乙一", 100)))

    conn.execute("DELETE FROM corpus.legal_resource")
    conn.commit()

    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement_condition") == 0


def test_a_law_another_law_delegates_to_cannot_be_deleted(conn: Conn, tmp_path: Path) -> None:
    """委任要件指向的單元所屬的法規，在被引用時刪不掉；刪持有要件的那一部則可以。"""
    stored_snapshot(
        conn, tmp_path, [_law("T0000001", "噪音管制法"), _law("T0000002", "振動管制法")]
    )
    importer.run(conn, tmp_path, Moj())
    load(
        conn,
        _doc(
            {
                "id": "duty",
                "law": "T0000001",
                "path": "art_1",
                "text": "為防制噪音，特制定本法。",
                "kind": "prescriptive",
                "deontic": "obligation",
                "conditions": [
                    {
                        "role": "duty",
                        "clause": 1,
                        "concept": "cod",
                        "comparator": "le",
                        "delegate": {"law": "T0000002", "path": "art_1"},
                    }
                ],
            }
        ),
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("DELETE FROM corpus.legal_resource WHERE local_id = 'T0000002'")
        conn.commit()
    conn.rollback()

    conn.execute("DELETE FROM corpus.legal_resource WHERE local_id = 'T0000001'")
    conn.execute("DELETE FROM corpus.legal_resource WHERE local_id = 'T0000002'")
    conn.commit()


def test_a_limit_cannot_be_marked_as_needing_judgement(conn: Conn, tmp_path: Path) -> None:
    """限值是數值，不是判斷：資料庫擋下。"""
    _first(conn, tmp_path, _law_v1())
    doc = _doc(_limit("乙一", 100))
    doc["statements"][0]["conditions"][0]["judgement"] = True

    with pytest.raises(AnnotationError, match="違反資料庫約束"):
        load(conn, doc)
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 0


def test_the_judgement_marker_defaults_to_false_and_is_loaded_when_given(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    doc = _doc(_duty())
    doc["statements"][0]["conditions"][0]["judgement"] = True
    load(conn, doc)

    rows = conn.execute(
        "SELECT condition_role, needs_assessment FROM annotation.statement_condition"
        " ORDER BY condition_role"
    ).fetchall()
    assert rows == [("scope", True), ("duty", False)] or rows == [("duty", False), ("scope", True)]


def test_a_sanction_can_be_an_order_and_can_repeat(conn: Conn, tmp_path: Path) -> None:
    """改善命令（非處罰）與按次處罰：效果值與旗標都存得進去，旗標預設為否。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一", "丙一")))
    doc = _doc(
        _duty(),
        {
            "id": "order",
            "law": LAW,
            "path": "art_2",
            "text": "乙一",
            "kind": "sanction",
            "sanction": {"punishes": "duty", "effect": "order"},
        },
        {
            "id": "again",
            "law": LAW,
            "path": "art_3",
            "text": "丙一",
            "kind": "sanction",
            "sanction": {"punishes": "duty", "effect": "fine", "repeating": True},
        },
    )
    load(conn, doc)

    rows = conn.execute(
        "SELECT legal_effect::text, per_occasion FROM annotation.sanction ORDER BY legal_effect"
    ).fetchall()
    assert rows == [("fine", True), ("order", False)]


def test_a_concept_with_a_broader_is_rejected(conn: Conn, tmp_path: Path) -> None:
    """詞彙沒有上下位：「甲屬於乙」的依據是用語定義，寫在概念上的 broader 整份拒絕。"""
    _first(conn, tmp_path, _law_v1())
    doc = _doc(_duty())
    doc["concepts"] = [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "cod", "label": "化學需氧量", "broader": "discharging"},
    ]
    with pytest.raises(AnnotationError, match="broader"):
        load(conn, doc)
    assert _scalar(conn, "SELECT count(*) FROM vocab.concept") == 0


def _definition_doc(text: str = "甲一") -> dict[str, Any]:
    doc = _doc()
    doc["scheme"] = {"code": f"def.{LAW}", "title": "本法用語", "law": LAW}
    doc["concepts"] = [
        {"code": "business", "label": "事業"},
        {"code": "factory", "label": "工廠"},
    ]
    doc["definitions"] = [
        {
            "id": "d1",
            "law": LAW,
            "path": "art_1",
            "text": text,
            "term": "business",
            "definition": "指工廠",
            "members": ["factory"],
        }
    ]
    return doc


def _defined(conn: Conn, on: date) -> int:
    return int(
        _scalar(
            conn, "SELECT count(*) FROM annotation.definitions_at(%s, 'zho', %s)", _law_id(conn), on
        )
    )


def test_a_definition_is_scoped_to_its_law_and_follows_the_text(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _definition_doc())

    assert loaded.definitions == 1
    assert _scalar(conn, "SELECT count(*) FROM annotation.term_definition_member") == 1
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM vocab.concept_scheme WHERE scope_resource_id IS NOT NULL",
        )
        == 1
    )
    assert _defined(conn, BEFORE) == 1


def test_a_definition_is_anchored_to_current_text(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    with pytest.raises(AnnotationError, match="用語定義"):
        load(conn, _definition_doc(text="不在語料裡的文字"))


def test_a_scheme_cannot_change_its_scope(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    load(conn, _definition_doc())
    unscoped = _definition_doc()
    unscoped["scheme"] = {"code": f"def.{LAW}", "title": "本法用語"}
    with pytest.raises(AnnotationError, match="適用範圍"):
        load(conn, unscoped)
