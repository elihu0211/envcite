"""The corpus schema's invariants and its per-provision resolution.

Validity is recorded per provision version, not per law: one promulgation can put
some articles in force at once and others years later (03-02 §5). These tests pin
that, and the constraints that keep versions from overlapping or crossing laws.
"""

from datetime import date

import psycopg
import pytest
from psycopg.types.range import Range

Conn = psycopg.Connection[tuple[object, ...]]

#: The sample law 水污染防治法 (`tests/sample.py`), resolved on a date its current version covers.
SAMPLE_AT_2020 = (
    "SELECT {} FROM corpus.legal_resource AS r,"
    " corpus.provisions_at(r.id, 'zho', DATE '2020-01-01') AS s"
    " WHERE r.local_id = 'O0040001' AND s.path = %s"
)


def _id(row: tuple[object, ...] | None) -> int:
    assert row is not None and isinstance(row[0], int)
    return row[0]


def _law(conn: Conn, pcode: str, *versions: tuple[date, str]) -> tuple[int, list[int]]:
    """A statute and its expressions, one per (version date, text status), in order."""
    law = _id(
        conn.execute(
            "INSERT INTO corpus.legal_resource (id_scheme, local_id, kind, document_type)"
            " VALUES ('moj_pcode', %s, 'statute', '法律') RETURNING id",
            (pcode,),
        ).fetchone()
    )
    ends = [on for on, _ in versions[1:]] + [None]
    expressions = []
    for (on, status), end in zip(versions, ends, strict=True):
        expressions.append(
            _id(
                conn.execute(
                    "INSERT INTO corpus.legal_expression (resource_id, language,"
                    " version_date, title, text_period, text_status, authority)"
                    " VALUES (%s, 'zho', %s, '甲法', %s, %s, 'official_db') RETURNING id",
                    (law, on, Range(on, end, "[)"), status),
                ).fetchone()
            )
        )
    return law, expressions


def _article(conn: Conn, law: int, number: str) -> int:
    return _id(
        conn.execute(
            "INSERT INTO corpus.provision (resource_id, provision_type, path, sort_key)"
            " VALUES (%s, 'article', %s, string_to_array(%s, '-')::integer[]) RETURNING id",
            (law, f"art_{number}", number),
        ).fetchone()
    )


def _version(
    conn: Conn,
    provision: int,
    expression: int,
    law: int,
    text: str | None,
    lo: date | None,
    hi: date | None,
    rule: str = "by_order",
) -> None:
    """A version in force over [lo, hi); lo None means promulgated but not yet in force."""
    conn.execute(
        "INSERT INTO corpus.provision_version"
        " (provision_id, expression_id, resource_id, content, in_force, commencement)"
        " VALUES (%s, %s, %s, %s, %s, %s::corpus.commencement_rule)",
        (
            provision,
            expression,
            law,
            text,
            Range(lo, hi, "[)") if lo else None,
            "promulgation" if lo else rule,
        ),
    )


def _at(conn: Conn, law: int, on: date) -> list[tuple[str, str, str | None]]:
    return [
        (str(path), str(status), text)  # type: ignore[misc]
        for path, status, text in conn.execute(
            "SELECT s.path, s.status, corpus.article_text(s.version_id)"
            "  FROM corpus.provisions_at(%s, 'zho', %s) AS s ORDER BY s.sort_key",
            (law, on),
        ).fetchall()
    ]


def test_pcode_shape_is_enforced(conn: Conn) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO corpus.legal_resource (id_scheme, local_id, kind, document_type)"
            " VALUES ('moj_pcode', 'air-pollution-control-act', 'statute', '法律')"
        )


def test_versions_of_one_provision_cannot_overlap(conn: Conn) -> None:
    law, (first, second) = _law(
        conn, "T0000001", (date(2020, 1, 1), "available"), (date(2022, 1, 1), "available")
    )
    article = _article(conn, law, "1")
    _version(conn, article, first, law, "舊文", date(2020, 1, 3), None)

    with pytest.raises(psycopg.errors.ExclusionViolation):
        _version(conn, article, second, law, "新文", date(2022, 1, 3), None)


def test_a_version_cannot_pair_a_provision_with_another_laws_expression(conn: Conn) -> None:
    law_a, _ = _law(conn, "T0000001", (date(2020, 1, 1), "available"))
    _, (other_expression,) = _law(conn, "T0000002", (date(2020, 1, 1), "available"))
    article = _article(conn, law_a, "1")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _version(conn, article, other_expression, law_a, "文", date(2020, 1, 3), None)


def test_partial_commencement_resolves_each_article_on_its_own(conn: Conn) -> None:
    """One amendment, two commencement dates — the shape of 廢棄物清理法 in 2026."""
    law, (old, new) = _law(
        conn, "T0000001", (date(2020, 1, 1), "available"), (date(2026, 7, 15), "available")
    )
    art10, art39, art39_2 = (_article(conn, law, n) for n in ("10", "39", "39-2"))
    # Article 10 takes effect on the third day; 39 and the added 39-2 two years later.
    _version(conn, art10, old, law, "第十條舊文", date(2020, 1, 3), date(2026, 7, 17))
    _version(conn, art10, new, law, "第十條新文", date(2026, 7, 17), None)
    _version(conn, art39, old, law, "第三十九條舊文", date(2020, 1, 3), date(2028, 7, 15))
    _version(conn, art39, new, law, "第三十九條新文", date(2028, 7, 15), None)
    _version(conn, art39_2, new, law, "第三十九條之二", None, None)

    assert _at(conn, law, date(2027, 1, 1)) == [
        ("art_10", "in_force", "第十條新文"),
        ("art_39", "in_force", "第三十九條舊文"),
        ("art_39-2", "not_yet_in_force", None),
    ]


def test_a_date_before_any_version_lists_nothing(conn: Conn) -> None:
    law, (only,) = _law(conn, "T0000001", (date(2020, 1, 1), "available"))
    _version(conn, _article(conn, law, "1"), only, law, "文", date(2020, 1, 3), None)

    assert _at(conn, law, date(2019, 12, 31)) == []


def test_text_unavailable_is_reported_not_replaced(conn: Conn) -> None:
    """A version known only from the history (沿革) has dates but no text."""
    law, (old, new) = _law(
        conn, "T0000001", (date(2000, 1, 1), "unavailable"), (date(2020, 1, 1), "available")
    )
    article = _article(conn, law, "1")
    _version(conn, article, old, law, None, date(2000, 1, 3), date(2020, 1, 3))
    _version(conn, article, new, law, "現行文字", date(2020, 1, 3), None)

    assert _at(conn, law, date(2010, 1, 1)) == [("art_1", "text_unavailable", None)]


def test_a_deleted_article_is_no_longer_in_force(conn: Conn) -> None:
    law, (only,) = _law(conn, "T0000001", (date(2020, 1, 1), "available"))
    _version(conn, _article(conn, law, "1"), only, law, "文", date(2020, 1, 3), date(2023, 1, 1))

    assert _at(conn, law, date(2024, 1, 1)) == [("art_1", "no_longer_in_force", None)]


def test_article_text_joins_paragraphs_in_reading_order(conn: Conn, sample: None) -> None:
    text = conn.execute(
        SAMPLE_AT_2020.format("corpus.article_text(s.version_id)"), ("art_7",)
    ).fetchone()

    assert text is not None
    first, second = str(text[0]).split("\n")
    assert first.startswith("事業、污水下水道系統")
    assert second.startswith("前項放流水標準")


def test_version_iri_follows_the_eli_template(conn: Conn, sample: None) -> None:
    iri = conn.execute(
        SAMPLE_AT_2020.format("corpus.version_iri(s.version_id)"), ("art_7/para_2",)
    ).fetchone()

    assert iri == ("https://env.law/eli/tw/law/O0040001/2018-06-13/zho/art_7/para_2",)
