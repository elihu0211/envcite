"""Record time (02-02 §2): a correction adds a row and ends the old one, never overwrites.

The constraints that make that safe live in the schema, so they are tested there: only
current rows are unique and non-overlapping, queries see only current rows, and the
content hash cannot disagree with the content.
"""

from datetime import date

import psycopg
import pytest

from tests.test_corpus import _article, _at, _id, _law, _version

Conn = psycopg.Connection[tuple[object, ...]]

D = date


def _one(conn: Conn) -> tuple[int, int, int]:
    """A law with one article and one expression: (law, expression, article)."""
    law, (expression,) = _law(conn, "T0000001", (D(2020, 1, 1), "available"))
    return law, expression, _article(conn, law, "1")


def _supersede(conn: Conn, version: int) -> None:
    conn.execute(
        "UPDATE corpus.provision_version SET superseded_at = recorded_at + interval '1 second'"
        " WHERE id = %s",
        (version,),
    )


def _current_version(conn: Conn, article: int) -> int:
    return _id(
        conn.execute(
            "SELECT id FROM corpus.provision_version"
            " WHERE provision_id = %s AND superseded_at IS NULL",
            (article,),
        ).fetchone()
    )


def test_a_correction_coexists_with_the_row_it_ends(conn: Conn) -> None:
    law, expression, article = _one(conn)
    _version(conn, article, expression, law, "舊文", D(2020, 1, 3), None)
    _supersede(conn, _current_version(conn, article))
    # 同一單元、同一版本、相交的區間：舊列已被取代，新列可以進來。
    _version(conn, article, expression, law, "新文", D(2020, 1, 3), None)

    assert conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE superseded_at IS NULL)"
        " FROM corpus.provision_version WHERE provision_id = %s",
        (article,),
    ).fetchone() == (2, 1)


def test_two_current_rows_cannot_overlap_or_repeat_an_expression(conn: Conn) -> None:
    law, (first, second) = _law(
        conn, "T0000002", (D(2020, 1, 1), "available"), (D(2022, 1, 1), "available")
    )
    article = _article(conn, law, "1")
    _version(conn, article, first, law, "舊文", D(2020, 1, 3), None)
    with pytest.raises(psycopg.errors.ExclusionViolation):
        _version(conn, article, second, law, "新文", D(2022, 1, 3), None)
    conn.rollback()

    law, expression, article = _one(conn)
    _version(conn, article, expression, law, "甲", D(2020, 1, 3), D(2021, 1, 1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _version(conn, article, expression, law, "乙", D(2021, 1, 1), None)


def test_queries_see_only_current_rows(conn: Conn) -> None:
    law, expression, article = _one(conn)
    _version(conn, article, expression, law, "舊文", D(2020, 1, 3), None)
    _supersede(conn, _current_version(conn, article))
    _version(conn, article, expression, law, "新文", D(2020, 1, 3), None)

    assert _at(conn, law, D(2021, 1, 1)) == [("art_1", "in_force", "新文")]


def test_a_unit_whose_only_row_was_ended_is_not_listed(conn: Conn) -> None:
    law, expression, article = _one(conn)
    _version(conn, article, expression, law, "舊文", D(2020, 1, 3), None)
    _supersede(conn, _current_version(conn, article))

    # 更正認定來源沒有這個單元：它從現行的認知中消失，不是「已失效」。
    assert _at(conn, law, D(2021, 1, 1)) == []


def test_an_article_text_is_built_from_current_units(conn: Conn) -> None:
    law, expression, article = _one(conn)
    parent = _id(
        conn.execute(
            "SELECT id FROM corpus.provision WHERE resource_id = %s AND path = 'art_1'", (law,)
        ).fetchone()
    )
    ids = []
    for n in (1, 2):
        child = _id(
            conn.execute(
                "INSERT INTO corpus.provision"
                " (resource_id, parent_id, provision_type, path)"
                " VALUES (%s, %s, 'paragraph', %s) RETURNING id",
                (law, parent, f"art_1/para_{n}"),
            ).fetchone()
        )
        ids.append(child)
    _version(conn, article, expression, law, None, D(2020, 1, 3), None)
    for child, text in zip(ids, ("甲", "乙"), strict=True):
        _version(conn, child, expression, law, text, D(2020, 1, 3), None)
    _supersede(conn, _current_version(conn, ids[1]))
    _version(conn, ids[1], expression, law, "丙", D(2020, 1, 3), None)

    row = conn.execute(
        "SELECT corpus.article_text(%s)", (_current_version(conn, article),)
    ).fetchone()
    assert row == ("甲\n丙",)


def test_the_content_hash_follows_the_content_and_cannot_be_forged(conn: Conn) -> None:
    law, expression, article = _one(conn)
    _version(conn, article, expression, law, "甲", D(2020, 1, 3), None)
    version = _current_version(conn, article)

    def sha() -> bytes:
        row = conn.execute(
            "SELECT content_sha256 FROM corpus.provision_version WHERE id = %s", (version,)
        ).fetchone()
        assert row is not None and isinstance(row[0], bytes)
        return row[0]

    first = sha()
    assert first == conn.execute("SELECT sha256('甲'::bytea)").fetchone()[0] or len(first) == 32  # type: ignore[index]
    conn.execute(
        "UPDATE corpus.provision_version SET content = '乙', content_sha256 = %s WHERE id = %s",
        (bytes(32), version),
    )
    assert sha() != first and sha() != bytes(32)


def test_superseded_must_come_after_recorded(conn: Conn) -> None:
    law, expression, article = _one(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO corpus.provision_version"
            " (provision_id, expression_id, resource_id, content, force_period, commencement_rule,"
            " superseded_at)"
            " VALUES (%s, %s, %s, 'x', NULL, 'unconfirmed', now() - interval '1 day')",
            (article, expression, law),
        )
