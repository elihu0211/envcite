"""Repeal, commencement, amendment history, provenance, and defined terms.

Every expectation here is traced to a statute rather than to intuition. The
provisions are quoted verbatim in 0008, which is where the rules live; this file
cites them by number.

  中央法規標準法 §13    commencement on the third day counted from publication
  §14                   commencement on a date the instrument states
  §21 iv                repeal because another statute now covers the matter
  §22 III               repeal takes effect on the third day, as §13 does
  §23                   a stated term expires on expiry — §22 does not apply

§22 III mirrors §13 and uses the same arithmetic; §23 is explicitly outside it.
Both directions are tested, because applying the third-day rule to a sunset and
failing to apply it to a repeal are two distinct ways of being a day or two wrong
with no signal.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

BASE = "https://env.law/eli/tw/law"


def _resource(
    conn: psycopg.Connection[tuple[object, ...]],
    slug: str,
    *,
    valid_to: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind, valid_from, valid_to)"
        " VALUES (%s, %s, 'statute', DATE '2000-01-01', %s) ON CONFLICT DO NOTHING",
        (slug, f"{BASE}/{slug}", valid_to),
    )


def _expression(conn: psycopg.Connection[tuple[object, ...]], slug: str) -> str:
    _resource(conn, slug)
    iri = f"{BASE}/{slug}/v1"
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, %s, '測試法', 'zh-hant', DATE '2020-01-01') ON CONFLICT DO NOTHING",
        (iri, slug),
    )
    return iri


def _article(conn: psycopg.Connection[tuple[object, ...]], slug: str, number: str) -> str:
    expression = _expression(conn, slug)
    article = f"{expression}/art_{number}"
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, %s, %s, %s, %s, '某條文。')",
        (article, slug, number, expression, article),
    )
    return article


# ---------------------------------------------------------------- termination


def test_repeal_takes_effect_on_the_third_day(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§22 III. Published 2024-03-01, so it ceases on 2024-03-03."""
    _resource(conn, "repealed-act", valid_to="2024-03-03")
    conn.execute(
        "UPDATE legal_resource SET termination = 'repealed',"
        " termination_promulgated = DATE '2024-03-01' WHERE id_local = 'repealed-act'"
    )
    conn.commit()

    row = conn.execute(
        "SELECT valid_to FROM legal_resource WHERE id_local = 'repealed-act'"
    ).fetchone()
    assert row is not None
    assert row[0] == dt.date(2024, 3, 3)


def test_repeal_on_the_publication_day_is_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The offset lives in one function; a hand-computed date that skips it is refused."""
    _resource(conn, "wrongday-act", valid_to="2024-03-01")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE legal_resource SET termination = 'repealed',"
            " termination_promulgated = DATE '2024-03-01' WHERE id_local = 'wrongday-act'"
        )


def test_sunset_carries_no_promulgation_date(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§23: expiry of a stated term is not a §22 repeal, so the third day does not apply."""
    _resource(conn, "sunset-act", valid_to="2024-12-31")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE legal_resource SET termination = 'sunset',"
            " termination_promulgated = DATE '2024-12-29' WHERE id_local = 'sunset-act'"
        )


def test_sunset_without_promulgation_is_fine(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _resource(conn, "sunset-ok-act", valid_to="2024-12-31")
    conn.execute(
        "UPDATE legal_resource SET termination = 'sunset' WHERE id_local = 'sunset-ok-act'"
    )
    conn.commit()

    row = conn.execute(
        "SELECT termination::text FROM legal_resource WHERE id_local = 'sunset-ok-act'"
    ).fetchone()
    assert row is not None
    assert row[0] == "sunset"


def test_termination_without_an_end_date_is_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _resource(conn, "noend-act")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE legal_resource SET termination = 'suspended' WHERE id_local = 'noend-act'"
        )


def test_repealed_by_requires_an_actual_repeal(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§21 iv names a repealing statute; naming one without repealing is incoherent."""
    _resource(conn, "old-act", valid_to="2024-03-03")
    _resource(conn, "new-act")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE legal_resource SET repealed_by = 'new-act' WHERE id_local = 'old-act'")


def test_a_norm_cannot_repeal_itself(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _resource(conn, "self-repeal-act", valid_to="2024-03-03")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE legal_resource SET termination = 'repealed',"
            " repealed_by = 'self-repeal-act' WHERE id_local = 'self-repeal-act'"
        )


# ---------------------------------------------------------------- commencement


def test_third_day_commencement_is_computed_not_stated(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§13, enforced. Promulgated 2024-03-01 means effective 2024-03-03."""
    _resource(conn, "commence-act")
    conn.execute(
        "INSERT INTO amendment (resource_id, promulgated, effective, rule)"
        " VALUES ('commence-act', DATE '2024-03-01', DATE '2024-03-03', 'third_day')"
    )
    conn.commit()

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO amendment (resource_id, promulgated, effective, rule)"
            " VALUES ('commence-act', DATE '2024-06-01', DATE '2024-06-01', 'third_day')"
        )


def test_specified_commencement_is_not_computed(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """§14: a stated date is whatever the instrument says, third day or not."""
    _resource(conn, "specified-act")
    conn.execute(
        "INSERT INTO amendment (resource_id, promulgated, effective, rule)"
        " VALUES ('specified-act', DATE '2024-03-01', DATE '2025-01-01', 'specified')"
    )
    conn.commit()

    row = conn.execute(
        "SELECT effective FROM amendment WHERE resource_id = 'specified-act'"
    ).fetchone()
    assert row is not None
    assert row[0] == dt.date(2025, 1, 1)


def test_retroactive_commencement_is_listed_not_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Rare but not unlawful. A CHECK would make correct data look like a bug."""
    _resource(conn, "retro-act")
    conn.execute(
        "INSERT INTO amendment (resource_id, promulgated, effective, rule)"
        " VALUES ('retro-act', DATE '2024-03-01', DATE '2024-01-01', 'specified')"
    )
    conn.commit()

    rows = conn.execute(
        "SELECT resource_id FROM amendment_retroactive WHERE resource_id = 'retro-act'"
    ).fetchall()
    assert [r[0] for r in rows] == ["retro-act"]


def test_a_deleted_article_is_recorded_by_number(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A deleted article has no text in the version that removed it, hence no iri."""
    _resource(conn, "delete-act")
    row = conn.execute(
        "INSERT INTO amendment (resource_id, promulgated, effective, rule)"
        " VALUES ('delete-act', DATE '2024-03-01', DATE '2024-03-03', 'third_day')"
        " RETURNING id"
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO amendment_change (amendment_id, number, kind) VALUES (%s, '5-1', 'deleted')",
        (row[0],),
    )
    conn.commit()

    changed = conn.execute(
        "SELECT number, kind::text FROM amendment_change WHERE amendment_id = %s", (row[0],)
    ).fetchall()
    assert changed == [("5-1", "deleted")]


# ---------------------------------------------------------------- provenance


def test_provenance_is_all_or_nothing(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """An authority claim with no retrieval time is a claim with no date behind it."""
    _resource(conn, "prov-act")
    iri = f"{BASE}/prov-act/v1"

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO legal_expression"
            " (iri, resource_id, title, language, valid_from, authority)"
            " VALUES (%s, 'prov-act', '測試法', 'zh-hant', DATE '2020-01-01', 'official_db')",
            (iri,),
        )


def test_unverified_text_is_enumerable(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """全國法規資料庫 states its own text yields to the promulgated wording.

    A corpus whose provenance was never recorded must not read as though it was.
    """
    _resource(conn, "noprov-act")
    _expression(conn, "noprov-act")
    conn.commit()

    rows = conn.execute(
        "SELECT resource_id FROM expression_without_provenance WHERE resource_id = 'noprov-act'"
    ).fetchall()
    assert [r[0] for r in rows] == ["noprov-act"]


def test_provenance_records_both_times(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Fetching today does not mean today's amendments are in it."""
    _resource(conn, "asof-act")
    conn.execute(
        "INSERT INTO legal_expression"
        " (iri, resource_id, title, language, valid_from,"
        "  authority, source_url, retrieved_at, source_as_of)"
        " VALUES (%s, 'asof-act', '測試法', 'zh-hant', DATE '2020-01-01',"
        "  'official_db', 'https://law.moj.gov.tw/x', TIMESTAMPTZ '2026-09-23 10:00+08',"
        "  DATE '2026-09-18')",
        (f"{BASE}/asof-act/v1",),
    )
    conn.commit()

    row = conn.execute(
        "SELECT source_as_of FROM legal_expression WHERE resource_id = 'asof-act'"
    ).fetchone()
    assert row is not None
    assert row[0] == dt.date(2026, 9, 18)


# ---------------------------------------------------------------- definitions


def test_the_same_term_means_different_things_in_different_statutes(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """「事業」 under 水污法 and under 空污法 are different things.

    A global glossary would merge them, silently, in favour of whichever was
    imported last.
    """
    water = _article(conn, "water-act", "2")
    air = _article(conn, "air-act", "3")
    for article, scope in ((water, "water-act"), (air, "air-act")):
        conn.execute(
            "INSERT INTO term_definition (term, defined_in, scope_resource)"
            " VALUES ('事業', %s, %s)",
            (article, scope),
        )
    conn.commit()

    rows = conn.execute(
        "SELECT scope_resource FROM term_definition WHERE term = '事業' ORDER BY scope_resource"
    ).fetchall()
    assert [r[0] for r in rows] == ["air-act", "water-act"]


def test_one_article_defines_a_term_once(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    article = _article(conn, "dup-act", "2")
    conn.execute(
        "INSERT INTO term_definition (term, defined_in, scope_resource)"
        " VALUES ('事業', %s, 'dup-act')",
        (article,),
    )
    conn.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO term_definition (term, defined_in, scope_resource)"
            " VALUES ('事業', %s, 'dup-act')",
            (article,),
        )


def test_a_term_defined_twice_in_one_statute_is_listed(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Legitimate case: an enforcement rule elaborating a term of its parent.

    A unique constraint on (term, scope) would have refused it. Extraction error
    and elaboration look alike from here, so both are listed and neither guessed at.
    """
    expr = _expression(conn, "twice-act")
    for number in ("2", "3"):
        article = f"{expr}/art_{number}"
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri, content)"
            " VALUES (%s, 'twice-act', %s, %s, %s, '某條文。')",
            (article, number, expr, article),
        )
        conn.execute(
            "INSERT INTO term_definition (term, defined_in, scope_resource)"
            " VALUES ('事業', %s, 'twice-act')",
            (article,),
        )
    conn.commit()

    rows = conn.execute(
        "SELECT term, definitions FROM term_defined_more_than_once"
        " WHERE scope_resource = 'twice-act'"
    ).fetchall()
    assert rows == [("事業", 2)]


def test_definition_article_stating_no_term_is_listed(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    article = _article(conn, "noterm-act", "2")
    conn.execute(
        "INSERT INTO subdivision_role (subdivision_iri, role) VALUES (%s, 'definition')",
        (article,),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT iri FROM definition_article_without_term WHERE resource_id = 'noterm-act'"
    ).fetchall()
    assert [r[0] for r in rows] == [article]
