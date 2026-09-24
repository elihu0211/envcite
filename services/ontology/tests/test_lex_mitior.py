"""Versions between the act and the disposition (行政罰法 §5).

What is tested here is mostly what the function *refuses* to do. §5 requires
comparing versions for favourability, and that comparison is a legal judgement —
a lower maximum fine is not automatically 「有利」. So the database enumerates
candidates completely and stops, and these tests assert that it stops: there is
no "applicable version" in the result, and nothing ranks them.

The three textual points that shape the signature are quoted in 0011.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

BASE = "https://env.law/eli/tw/law"


def _law(
    conn: psycopg.Connection[tuple[object, ...]],
    slug: str,
    versions: list[tuple[str, str | None, str]],
    *,
    kind: str = "statute",
) -> None:
    """A law with the given (valid_from, valid_to, label) texts."""
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, %s::norm_kind)",
        (slug, f"{BASE}/{slug}", kind),
    )
    for i, (frm, to, label) in enumerate(versions, 1):
        conn.execute(
            "INSERT INTO legal_expression"
            " (iri, resource_id, title, language, valid_from, valid_to, version_label)"
            " VALUES (%s, %s, '測試法', 'zh-hant', %s, %s, %s)",
            (f"{BASE}/{slug}/v{i}", slug, frm, to, label),
        )
    conn.commit()


def _candidates(
    conn: psycopg.Connection[tuple[object, ...]],
    slug: str,
    act: str,
    disposition: str,
) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT version_label, kind::text, in_force_at_act, in_force_at_disposition"
        " FROM candidate_versions(%s, 'zh-hant', %s::date, %s::date)",
        (slug, act, disposition),
    ).fetchall()


def test_every_version_in_the_window_is_listed(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Including the ones in force only in between — they are §5 candidates too."""
    _law(
        conn,
        "amended-act",
        [
            ("2018-01-01", "2020-01-01", "行為前"),
            ("2020-01-01", "2022-01-01", "行為時"),
            ("2022-01-01", "2024-01-01", "中間"),
            ("2024-01-01", None, "裁處時"),
        ],
    )

    rows = _candidates(conn, "amended-act", "2021-06-01", "2024-06-01")
    assert [r[0] for r in rows] == ["行為時", "中間", "裁處時"]


def test_the_two_anchors_are_marked(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    _law(
        conn,
        "marked-act",
        [
            ("2020-01-01", "2022-01-01", "舊"),
            ("2022-01-01", None, "新"),
        ],
    )

    rows = _candidates(conn, "marked-act", "2021-06-01", "2023-06-01")
    assert rows == [
        ("舊", "statute", True, False),
        ("新", "statute", False, True),
    ]


def test_one_version_can_be_both(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """No amendment in between means no §5 question — and the data says so."""
    _law(conn, "stable-act", [("2020-01-01", None, "唯一")])

    rows = _candidates(conn, "stable-act", "2021-06-01", "2023-06-01")
    assert rows == [("唯一", "statute", True, True)]


def test_the_disposition_day_itself_counts(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A text taking effect on the disposition date is squarely within §5."""
    _law(
        conn,
        "sameday-act",
        [
            ("2020-01-01", "2023-06-01", "舊"),
            ("2023-06-01", None, "當日生效"),
        ],
    )

    rows = _candidates(conn, "sameday-act", "2021-06-01", "2023-06-01")
    assert [r[0] for r in rows] == ["舊", "當日生效"]
    assert rows[1][3] is True


def test_the_norm_kind_comes_back(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """§5 says 「法律或自治條例」 and not 法規命令, and whether it reaches delegated
    regulations is contested. The function returns the kind and takes no position;
    scoping the rule is the caller's, because it is a legal position."""
    _law(conn, "reg-act", [("2020-01-01", None, "命令")], kind="regulation")

    rows = _candidates(conn, "reg-act", "2021-06-01", "2023-06-01")
    assert rows == [("命令", "regulation", True, True)]


def test_nothing_ranks_the_candidates(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Favourability is a legal judgement; a column expressing it would make that
    judgement silently on behalf of whoever read the result."""
    _law(conn, "norank-act", [("2020-01-01", "2022-01-01", "舊"), ("2022-01-01", None, "新")])

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM candidate_versions('norank-act', 'zh-hant',"
            " DATE '2021-01-01', DATE '2023-01-01')"
        )
        columns = [d.name for d in cur.description or []]

    assert columns, "no columns at all"
    forbidden = ("applicable", "favourable", "favorable", "best", "rank", "score")
    assert not [c for c in columns if any(w in c for w in forbidden)]


def test_an_act_after_its_disposition_is_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Incoherent input, not an empty corpus — an empty result would read as the latter."""
    _law(conn, "order-act", [("2020-01-01", None, "唯一")])

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _candidates(conn, "order-act", "2024-01-01", "2020-01-01")


def test_an_unknown_law_is_distinguishable_from_no_version(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The same distinction 0004 draws, for the same reason."""
    with pytest.raises(psycopg.errors.NoDataFound):
        _candidates(conn, "nonexistent-act", "2020-01-01", "2024-01-01")


def test_a_law_with_no_version_in_the_window_returns_nothing(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _law(conn, "later-act", [("2025-01-01", None, "尚未生效")])

    assert _candidates(conn, "later-act", "2020-01-01", "2024-01-01") == []


def test_amended_laws_are_enumerable(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """A consultation spanning one of these must not be answered from one version."""
    _law(conn, "multi-act", [("2020-01-01", "2022-01-01", "舊"), ("2022-01-01", None, "新")])
    _law(conn, "single-act", [("2020-01-01", None, "唯一")])

    rows = conn.execute(
        "SELECT resource_id, versions FROM resource_amended_between"
        " WHERE resource_id IN ('multi-act', 'single-act')"
    ).fetchall()
    assert rows == [("multi-act", 2)]


def test_the_earliest_version_sorts_first(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    _law(
        conn,
        "sorted-act",
        [
            ("2022-01-01", None, "新"),
            ("2020-01-01", "2022-01-01", "舊"),
        ],
    )

    rows = conn.execute(
        "SELECT valid_from FROM candidate_versions('sorted-act', 'zh-hant',"
        " DATE '2021-01-01', DATE '2023-01-01')"
    ).fetchall()
    assert [r[0] for r in rows] == [dt.date(2020, 1, 1), dt.date(2022, 1, 1)]
