"""Article text, article membership, and what a role permits.

Two invariants are worth testing against a real PostgreSQL rather than asserted
in prose, because both are enforced by constraints whose correctness is not
obvious from reading them:

  * `article_iri` really is the root of the ancestor chain — enforced inductively
    by a composite foreign key, not by a trigger walking the tree.
  * only articles carry text — which keeps the paragraph-location question open
    instead of answering it by accident.
"""

from __future__ import annotations

import psycopg
import pytest

BASE = "https://env.law/eli/tw/law"


def _law_with_article(
    conn: psycopg.Connection[tuple[object, ...]], slug: str, *, content: str | None = None
) -> tuple[str, str]:
    """One law, one text, one article. Returns (expression_iri, article_iri)."""
    base = f"{BASE}/{slug}"
    expression = f"{base}/v1"
    article = f"{expression}/art_1"
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')",
        (slug, base),
    )
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, %s, '測試法', 'zh-hant', DATE '2020-01-01')",
        (expression, slug),
    )
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, %s, '1', %s, %s, %s)",
        (article, slug, expression, article, content),
    )
    conn.commit()
    return expression, article


def _paragraph(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    iri: str,
    slug: str,
    expression: str,
    parent: str,
    article: str,
    number: str = "1-1",
    content: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, parent_iri, article_iri, content)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (iri, slug, number, expression, parent, article, content),
    )


def test_article_text_round_trips(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _, article = _law_with_article(conn, "text-act", content="公私場所不得排放空氣污染物。")

    row = conn.execute("SELECT content FROM subdivision WHERE iri = %s", (article,)).fetchone()
    assert row is not None
    assert row[0] == "公私場所不得排放空氣污染物。"


def test_empty_content_is_refused(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """ "No text" and "text that is empty" are different facts."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _law_with_article(conn, "empty-act", content="")


def test_missing_content_is_enumerable(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """An import covering 60% of articles must not look like one covering all."""
    _, article = _law_with_article(conn, "gap-act")

    rows = conn.execute(
        "SELECT iri FROM article_missing_content WHERE resource_id = 'gap-act'"
    ).fetchall()
    assert [r[0] for r in rows] == [article]


def test_top_level_must_be_its_own_article(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    base = f"{BASE}/self-act"
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES ('self-act', %s, 'statute')",
        (base,),
    )
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, 'self-act', '測試法', 'zh-hant', DATE '2020-01-01')",
        (f"{base}/v1",),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri)"
            " VALUES (%s, 'self-act', '1', %s, %s)",
            (f"{base}/v1/art_1", f"{base}/v1", f"{base}/v1/art_999"),
        )


def test_paragraph_inherits_its_article(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    expression, article = _law_with_article(conn, "nest-act", content="第一條本文。")
    _paragraph(
        conn,
        iri=f"{expression}/art_1_par_1",
        slug="nest-act",
        expression=expression,
        parent=article,
        article=article,
    )
    conn.commit()

    row = conn.execute(
        "SELECT article_iri FROM subdivision WHERE iri = %s", (f"{expression}/art_1_par_1",)
    ).fetchone()
    assert row is not None
    assert row[0] == article


def test_paragraph_cannot_claim_another_article(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The composite FK makes "article_iri is my ancestor" inductive."""
    expression, article = _law_with_article(conn, "claim-act", content="第一條本文。")
    other_article = f"{expression}/art_2"
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, 'claim-act', '2', %s, %s, '第二條本文。')",
        (other_article, expression, other_article),
    )
    conn.commit()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _paragraph(
            conn,
            iri=f"{expression}/art_1_par_1",
            slug="claim-act",
            expression=expression,
            parent=article,
            article=other_article,  # parent belongs to art_1, not art_2
        )


def test_paragraph_may_not_carry_text(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Two copies of the same words drift, and nothing would notice."""
    expression, article = _law_with_article(conn, "frag-act", content="第一條本文。")

    with pytest.raises(psycopg.errors.CheckViolation):
        _paragraph(
            conn,
            iri=f"{expression}/art_1_par_1",
            slug="frag-act",
            expression=expression,
            parent=article,
            article=article,
            content="本文的一部分。",
        )


def test_deleting_an_article_removes_its_paragraphs(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    expression, article = _law_with_article(conn, "cascade-act", content="第一條本文。")
    _paragraph(
        conn,
        iri=f"{expression}/art_1_par_1",
        slug="cascade-act",
        expression=expression,
        parent=article,
        article=article,
    )
    conn.commit()

    conn.execute("DELETE FROM subdivision WHERE iri = %s", (article,))
    remaining = conn.execute(
        "SELECT count(*) FROM subdivision WHERE resource_id = 'cascade-act'"
    ).fetchone()
    assert remaining is not None
    assert remaining[0] == 0


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("obligation", True),
        ("prohibition", True),
        ("definition", False),
        ("procedure", False),
        ("sanction", False),
        ("authorization", False),
        ("commencement", False),
    ],
)
def test_only_duties_ground_a_sanction(
    conn: psycopg.Connection[tuple[object, ...]], role: str, expected: bool
) -> None:
    """A definition states what a word means; it imposes nothing to violate."""
    row = conn.execute("SELECT may_ground_sanction(%s::norm_role)", (role,)).fetchone()
    assert row is not None
    assert row[0] is expected


def test_every_role_is_decided(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """A new enum member must not silently evaluate to NULL.

    `may_ground_sanction` is a bare IN, so an unlisted role yields false rather
    than an error — safe, but only as long as someone checks. This is that check.
    """
    rows = conn.execute(
        "SELECT r::text, may_ground_sanction(r) FROM unnest(enum_range(NULL::norm_role)) AS r"
    ).fetchall()
    assert len(rows) == 10
    assert all(value is not None for _, value in rows)
    assert {name for name, value in rows if value} == {"obligation", "prohibition"}


def test_groundable_view_lists_only_duties(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _, duty = _law_with_article(conn, "duty-act", content="應於期限內申報。")
    _, defn = _law_with_article(conn, "defn-act", content="本法所稱事業，指…")
    conn.execute(
        "INSERT INTO subdivision_role (subdivision_iri, role) VALUES (%s, 'obligation')",
        (duty,),
    )
    conn.execute(
        "INSERT INTO subdivision_role (subdivision_iri, role) VALUES (%s, 'definition')",
        (defn,),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT iri FROM article_groundable WHERE resource_id IN ('duty-act', 'defn-act')"
    ).fetchall()
    assert [r[0] for r in rows] == [duty]


def test_article_may_hold_several_roles(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """A paragraph imposing a duty and the next defining its terms, in one article."""
    _, article = _law_with_article(conn, "multi-act", content="應申報。本法所稱申報，指…")
    for role in ("obligation", "definition"):
        conn.execute(
            "INSERT INTO subdivision_role (subdivision_iri, role) VALUES (%s, %s::norm_role)",
            (article, role),
        )
    conn.commit()

    rows = conn.execute(
        "SELECT role::text FROM subdivision_role WHERE subdivision_iri = %s ORDER BY role::text",
        (article,),
    ).fetchall()
    assert [r[0] for r in rows] == ["definition", "obligation"]


def test_missing_role_is_enumerable(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _, article = _law_with_article(conn, "norole-act", content="某條文。")

    rows = conn.execute(
        "SELECT iri FROM article_missing_role WHERE resource_id = 'norole-act'"
    ).fetchall()
    assert [r[0] for r in rows] == [article]
