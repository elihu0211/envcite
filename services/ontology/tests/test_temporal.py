"""Point-in-time resolution, against a real PostgreSQL.

The rule these tests exist for: when the corpus holds no version in force on the
requested date, the answer is "I do not know" — never the current text. Falling
back would produce an answer indistinguishable from a correct one, which is the
failure mode this whole layer is built to prevent.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from env_law.store import OntologyStore, VersionStatus


@pytest.fixture
def conn(dsn: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(dsn) as c:
        yield c
        c.rollback()


def _versioned_law(conn: psycopg.Connection[tuple[object, ...]], slug: str) -> None:
    """A law with two texts: 2010-01-01 to 2020-01-01, then 2020-01-01 onwards."""
    base = f"https://env.law/eli/tw/law/{slug}"
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')",
        (slug, base),
    )
    for suffix, title, frm, to in (
        ("v1", "舊版標題", "2010-01-01", "2020-01-01"),
        ("v2", "新版標題", "2020-01-01", None),
    ):
        conn.execute(
            "INSERT INTO legal_expression"
            " (iri, resource_id, title, language, valid_from, valid_to)"
            " VALUES (%s, %s, %s, 'zh-hant', %s, %s)",
            (f"{base}/{suffix}", slug, title, frm, to),
        )
    conn.execute(
        "INSERT INTO subdivision (iri, resource_id, number, expression_iri)"
        " VALUES (%s, %s, '1', %s)",
        (f"{base}/v2/art_1", slug, f"{base}/v2"),
    )
    conn.commit()


def test_overlapping_versions_are_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Two texts in force at once is a contradiction, not an ambiguity to resolve."""
    base = "https://env.law/eli/tw/law/overlap-act"
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')",
        ("overlap-act", base),
    )
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from, valid_to)"
        " VALUES (%s, 'overlap-act', 'A', 'zh-hant', DATE '2010-01-01', DATE '2020-01-01')",
        (f"{base}/a",),
    )

    with pytest.raises(psycopg.errors.ExclusionViolation):
        conn.execute(
            "INSERT INTO legal_expression"
            " (iri, resource_id, title, language, valid_from, valid_to)"
            " VALUES (%s, 'overlap-act', 'B', 'zh-hant', DATE '2015-01-01', DATE '2025-01-01')",
            (f"{base}/b",),
        )


def test_adjacent_versions_are_allowed(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """[from, to) is half-open: one text ends exactly where the next begins."""
    _versioned_law(conn, "adjacent-act")

    rows = conn.execute(
        "SELECT count(*) FROM legal_expression WHERE resource_id = 'adjacent-act'"
    ).fetchone()

    assert rows == (2,)


def test_effective_date_follows_article_13(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§13: counted from the publication day, the third day — publication plus two."""
    row = conn.execute("SELECT effective_from_publication(DATE '2026-01-01')::text").fetchone()

    assert row == ("2026-01-03",)


def test_resolution_picks_the_text_in_force(
    dsn: str, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    _versioned_law(conn, "intime-act")

    with OntologyStore.from_dsn(dsn) as store:
        old = store.get_law("intime-act", as_of="2015-06-01")
        new = store.get_law("intime-act", as_of="2021-06-01")
        current = store.get_law("intime-act")

    assert old is not None and old.title == "舊版標題"
    assert new is not None and new.title == "新版標題"
    assert current is not None and current.title == "新版標題"


def test_a_date_before_any_version_is_not_answered_with_the_current_text(
    dsn: str, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    """The failure this layer exists to prevent."""
    _versioned_law(conn, "gap-act")

    with OntologyStore.from_dsn(dsn) as store:
        status, record = store.resolve("gap-act", as_of="1990-01-01")

    assert status is VersionStatus.NO_VERSION
    assert record is None


def test_unknown_law_and_unheld_version_are_different_statuses(
    dsn: str, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    """One means the corpus lacks the law; the other means it lacks that text."""
    _versioned_law(conn, "status-act")

    with OntologyStore.from_dsn(dsn) as store:
        missing, _ = store.resolve("no-such-act")
        unheld, _ = store.resolve("status-act", as_of="1990-01-01")

    assert missing is VersionStatus.UNKNOWN_LAW
    assert unheld is VersionStatus.NO_VERSION


def test_articles_follow_the_resolved_version(
    dsn: str, conn: psycopg.Connection[tuple[object, ...]]
) -> None:
    """Articles belong to a text; asking at a date the text did not exist yields none."""
    _versioned_law(conn, "articles-act")

    with OntologyStore.from_dsn(dsn) as store:
        current = store.list_articles("articles-act")
        older = store.list_articles("articles-act", as_of="2015-06-01")
        before_any = store.list_articles("articles-act", as_of="1990-01-01")

    assert current is not None and [a.number for a in current] == ["1"]
    # The 2010 text exists but this fixture gave it no articles — a fact about the
    # fixture, not a missing version, so [] rather than None.
    assert older == []
    assert before_any is None


def test_an_article_may_not_be_its_own_parent(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _versioned_law(conn, "parent-act")
    iri = "https://env.law/eli/tw/law/parent-act/v2/art_1"

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE subdivision SET parent_iri = %s WHERE iri = %s", (iri, iri))


def test_article_may_not_belong_to_another_laws_text(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The denormalised resource_id must agree with the expression's resource."""
    _versioned_law(conn, "one-act")
    _versioned_law(conn, "other-act")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO subdivision (iri, resource_id, number, expression_iri)"
            " VALUES (%s, 'one-act', '9', %s)",
            (
                "https://env.law/eli/tw/law/one-act/v2/art_9",
                "https://env.law/eli/tw/law/other-act/v2",
            ),
        )
