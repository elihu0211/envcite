"""The schema-level rules of 01-02, exercised against a real PostgreSQL.

These are not unit tests of Python. Every assertion here is about something the
database refuses to store, because that is where the rule has to live: a rule
enforced in application code holds until the second writer appears.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest


@pytest.fixture
def conn(dsn: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """A connection whose writes are rolled back, so cases cannot see each other."""
    with psycopg.connect(dsn) as c:
        yield c
        c.rollback()


def _resource(conn: psycopg.Connection[tuple[object, ...]], slug: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, %s)",
        (slug, f"https://env.law/eli/tw/law/{slug}", kind),
    )


def test_incorporation_may_not_pin_a_version(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§17: 準用 points at the norm, not at a version of it.

    Pinning would make the system keep applying the old text after an amendment,
    silently. The database refuses it rather than trusting the writer to know.
    """
    _resource(conn, "special-act", "statute")
    _resource(conn, "general-act", "statute")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO norm_relation (source_id, target_id, kind, target_version)"
            " VALUES (%s, %s, 'incorporates', DATE '2020-01-01')",
            ("special-act", "general-act"),
        )


def test_citation_may_pin_a_version(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """A citation refers to text, so the version it referred to is the fact."""
    _resource(conn, "citing-act", "statute")
    _resource(conn, "cited-act", "statute")

    conn.execute(
        "INSERT INTO norm_relation (source_id, target_id, kind, target_version)"
        " VALUES (%s, %s, 'cites', DATE '2020-01-01')",
        ("citing-act", "cited-act"),
    )


def test_a_norm_may_not_relate_to_itself(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _resource(conn, "lonely-act", "statute")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO norm_relation (source_id, target_id, kind) VALUES (%s, %s, 'cites')",
            ("lonely-act", "lonely-act"),
        )


def test_whole_norm_relation_can_be_stated_only_once(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """`source_iri IS NULL` means "the norm as a whole" — one fact, not many."""
    _resource(conn, "a-act", "statute")
    _resource(conn, "b-act", "statute")
    conn.execute(
        "INSERT INTO norm_relation (source_id, target_id, kind) VALUES (%s, %s, 'cites')",
        ("a-act", "b-act"),
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO norm_relation (source_id, target_id, kind) VALUES (%s, %s, 'cites')",
            ("a-act", "b-act"),
        )


def test_regulation_may_not_authorise_a_statute(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§11: an authorising norm must outrank what it authorises.

    The relation is storable — rank is a judgement about a pair, not a column
    constraint — so the violation surfaces as a row in a view rather than a
    rejected write. That is the honest shape: it is a finding, not a typo.
    """
    _resource(conn, "some-regulation", "regulation")
    _resource(conn, "some-statute", "statute")
    conn.execute(
        "INSERT INTO norm_relation (source_id, target_id, kind) VALUES (%s, %s, 'authorizes')",
        ("some-regulation", "some-statute"),
    )

    rows = conn.execute(
        "SELECT source_id, target_id FROM norm_hierarchy_violation WHERE source_id = %s",
        ("some-regulation",),
    ).fetchall()

    assert rows == [("some-regulation", "some-statute")]


def test_statute_authorising_a_regulation_is_not_a_violation(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _resource(conn, "parent-act", "statute")
    _resource(conn, "child-regulation", "regulation")
    conn.execute(
        "INSERT INTO norm_relation (source_id, target_id, kind) VALUES (%s, %s, 'authorizes')",
        ("parent-act", "child-regulation"),
    )

    rows = conn.execute(
        "SELECT 1 FROM norm_hierarchy_violation WHERE source_id = %s", ("parent-act",)
    ).fetchall()

    assert rows == []


def test_referenced_but_absent_norms_are_enumerable(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The property the competent-authority filter could not offer.

    A norm pointed at but not held is a gap the system lists by itself, with no
    external population to compare against. It lives in its own table precisely
    because `norm_relation.target_id` is a foreign key: a gap that cannot be
    stored cannot be enumerated.
    """
    _resource(conn, "referring-act", "statute")
    conn.execute(
        "INSERT INTO unresolved_reference (source_id, target_ref, kind)"
        " VALUES (%s, %s, 'incorporates')",
        ("referring-act", "administrative-procedure-act"),
    )

    rows = conn.execute(
        "SELECT missing_ref, referenced_by FROM corpus_gap WHERE missing_ref = %s",
        ("administrative-procedure-act",),
    ).fetchall()

    assert rows == [("administrative-procedure-act", 1)]


def test_a_gap_closes_when_the_target_arrives(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Resolution is the target existing — not someone remembering to tick a box."""
    _resource(conn, "referring-act-2", "statute")
    conn.execute(
        "INSERT INTO unresolved_reference (source_id, target_ref, kind)"
        " VALUES (%s, %s, 'incorporates')",
        ("referring-act-2", "late-arriving-act"),
    )
    _resource(conn, "late-arriving-act", "statute")

    rows = conn.execute(
        "SELECT 1 FROM corpus_gap WHERE missing_ref = %s", ("late-arriving-act",)
    ).fetchall()

    assert rows == []


def test_validity_period_must_be_ordered(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO legal_resource (id_local, iri, kind, valid_from, valid_to)"
            " VALUES (%s, %s, 'statute', DATE '2020-01-01', DATE '2019-01-01')",
            ("backwards-act", "https://env.law/eli/tw/law/backwards-act"),
        )


def test_competent_authority_is_optional(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """It is metadata. A missing value must never remove a norm from the corpus."""
    _resource(conn, "unattributed-act", "statute")

    row = conn.execute(
        "SELECT competent_authority FROM legal_resource WHERE id_local = %s",
        ("unattributed-act",),
    ).fetchone()

    assert row == (None,)
