"""Penalties, and the statutory amounts read out of the article text.

The parser is the part worth testing hardest. A wrong amount is not a wrong
answer that looks wrong — it is a plausible number attached to a real article,
and the only thing that would contradict it is the text it was read from.

The legal ground for the expectations here:

  行政罰法 §18 II   the statutory maximum may be exceeded where the gain did
  行政罰法 §18 III  the statutory minimum may be gone below on mitigation
  刑法 §10 I        以上、以下 include the stated figure — the range is closed

Hence: what is stored is the statutory range, not a prediction of the penalty.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

BASE = "https://env.law/eli/tw/law"


def _article(
    conn: psycopg.Connection[tuple[object, ...]],
    slug: str,
    number: str,
    *,
    content: str,
    role: str | None = None,
) -> str:
    """One law, one text, one article — created once per law, reused after."""
    base = f"{BASE}/{slug}"
    expression = f"{base}/v1"
    article = f"{expression}/art_{number}"
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')"
        " ON CONFLICT DO NOTHING",
        (slug, base),
    )
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, %s, '測試法', 'zh-hant', DATE '2020-01-01') ON CONFLICT DO NOTHING",
        (expression, slug),
    )
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (article, slug, number, expression, article, content),
    )
    if role is not None:
        conn.execute(
            "INSERT INTO subdivision_role (subdivision_iri, role) VALUES (%s, %s::norm_role)",
            (article, role),
        )
    conn.commit()
    return article


def _numeral(conn: psycopg.Connection[tuple[object, ...]], s: str) -> Decimal | None:
    row = conn.execute("SELECT chinese_numeral(%s)", (s,)).fetchone()
    assert row is not None
    value = row[0]
    assert value is None or isinstance(value, Decimal)
    return value


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("六千", 6000),
        ("一千", 1000),
        ("十", 10),
        ("二十", 20),
        ("十萬", 100000),  # place value leads; the digit is implied
        ("三十萬", 300000),
        ("一百萬", 1_000_000),
        ("二千萬", 20_000_000),
        ("一萬五千", 15000),
        ("一千零五十", 1050),
        ("一億", 100_000_000),
        ("兩千萬", 20_000_000),  # 兩 for 二
        ("壹佰萬", 1_000_000),  # formal forms, as used on instruments
        ("6,000", 6000),  # Arabic, as the gazette sometimes prints it
        ("500", 500),
    ],
)
def test_numerals_read_correctly(
    conn: psycopg.Connection[tuple[object, ...]], written: str, expected: int
) -> None:
    assert _numeral(conn, written) == expected


@pytest.mark.parametrize("written", ["", "abc", "十萬元", "若干"])
def test_unreadable_numerals_are_null_not_a_guess(
    conn: psycopg.Connection[tuple[object, ...]], written: str
) -> None:
    """A wrong amount is worse than no amount: it is indistinguishable from a right one."""
    assert _numeral(conn, written) is None


@pytest.mark.parametrize(
    ("amount_text", "low", "high"),
    [
        ("處新臺幣十萬元以上一百萬元以下罰鍰", 100_000, 1_000_000),
        ("處新臺幣六千元以下罰鍰", None, 6000),
        ("處新臺幣一千元以上罰鍰", 1000, None),
        ("處新臺幣一百萬元以上二千萬元以下罰鍰", 1_000_000, 20_000_000),
    ],
)
def test_bounds_are_read_from_the_clause(
    conn: psycopg.Connection[tuple[object, ...]],
    amount_text: str,
    low: int | None,
    high: int | None,
) -> None:
    row = conn.execute(
        "SELECT read_statutory_fine_min(%s), read_statutory_fine_max(%s)",
        (amount_text, amount_text),
    ).fetchone()
    assert row is not None
    assert row[0] == low
    assert row[1] == high


def test_absent_floor_is_null_not_zero(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """「六千元以下」 states no floor.

    Zero would be a number the legislature did not write, and 行政罰法 §18 III
    computes against 法定罰鍰最低額 on mitigation — a fabricated 0 would make that
    rule produce an answer instead of revealing that the statute is silent.
    """
    row = conn.execute("SELECT read_statutory_fine_min('處新臺幣六千元以下罰鍰')").fetchone()
    assert row is not None
    assert row[0] is None


def test_amounts_are_derived_from_the_text(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    article = _article(
        conn, "fine-act", "50", content="違反第二十條者，處新臺幣十萬元以上一百萬元以下罰鍰。"
    )
    conn.execute(
        "INSERT INTO sanction (sanction_iri, effect, amount_text)"
        " VALUES (%s, 'fine', '處新臺幣十萬元以上一百萬元以下罰鍰')",
        (article,),
    )
    conn.commit()

    row = conn.execute(
        "SELECT statutory_fine_min, statutory_fine_max FROM sanction WHERE sanction_iri = %s",
        (article,),
    ).fetchone()
    assert row is not None
    assert (row[0], row[1]) == (100_000, 1_000_000)


def test_amounts_cannot_be_written_by_hand(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The columns are GENERATED: there is no way to state a figure the text lacks."""
    article = _article(conn, "handwrite-act", "50", content="處新臺幣六千元以下罰鍰。")

    with pytest.raises(psycopg.errors.GeneratedAlways):
        conn.execute(
            "INSERT INTO sanction (sanction_iri, effect, amount_text, statutory_fine_max)"
            " VALUES (%s, 'fine', '處新臺幣六千元以下罰鍰', 999999)",
            (article,),
        )


def test_amounts_follow_an_amendment(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Change the clause, the figures change with it — there is no second copy."""
    article = _article(conn, "amend-fine-act", "50", content="處新臺幣六千元以下罰鍰。")
    conn.execute(
        "INSERT INTO sanction (sanction_iri, effect, amount_text)"
        " VALUES (%s, 'fine', '處新臺幣六千元以下罰鍰')",
        (article,),
    )
    conn.commit()

    conn.execute(
        "UPDATE sanction SET amount_text = '處新臺幣二萬元以上十萬元以下罰鍰'"
        " WHERE sanction_iri = %s",
        (article,),
    )
    row = conn.execute(
        "SELECT statutory_fine_min, statutory_fine_max FROM sanction WHERE sanction_iri = %s",
        (article,),
    ).fetchone()
    assert row is not None
    assert (row[0], row[1]) == (20_000, 100_000)


def test_fine_must_state_an_amount(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    article = _article(conn, "noamount-act", "50", content="違反者處罰。")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO sanction (sanction_iri, effect) VALUES (%s, 'fine')",
            (article,),
        )


def test_non_monetary_effect_may_not_carry_an_amount(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    article = _article(conn, "suspend-act", "50", content="得命停工。")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO sanction (sanction_iri, effect, amount_text)"
            " VALUES (%s, 'suspension', '處新臺幣六千元以下罰鍰')",
            (article,),
        )


def test_one_article_may_carry_several_effects(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A fine plus an order to remedy is two effects, not one compound thing."""
    article = _article(conn, "both-act", "50", content="處新臺幣六千元以下罰鍰，並命限期改善。")
    conn.execute(
        "INSERT INTO sanction (sanction_iri, effect, amount_text)"
        " VALUES (%s, 'fine', '處新臺幣六千元以下罰鍰')",
        (article,),
    )
    conn.execute(
        "INSERT INTO sanction (sanction_iri, effect) VALUES (%s, 'order_to_remedy')",
        (article,),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT effect::text FROM sanction WHERE sanction_iri = %s ORDER BY effect::text",
        (article,),
    ).fetchall()
    assert [r[0] for r in rows] == ["fine", "order_to_remedy"]


def test_unreadable_amount_is_enumerable(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """NULL bounds from an unparsable clause must not read as 「條文未定上限」."""
    article = _article(conn, "odd-act", "50", content="處以相當之罰鍰。")
    conn.execute(
        "INSERT INTO sanction (sanction_iri, effect, amount_text)"
        " VALUES (%s, 'fine', '處以相當之罰鍰')",
        (article,),
    )
    conn.commit()

    rows = conn.execute("SELECT sanction_iri FROM sanction_amount_unreadable").fetchall()
    assert [r[0] for r in rows] == [article]


def test_penalty_on_a_definition_is_listed_not_refused(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A penalty resting on a definition is a modelling error worth seeing.

    Refusing the insert would hide the disagreement: the role assignment may be
    what is wrong, and a rejected write leaves nothing to inspect.
    """
    duty = _article(conn, "role-act", "20", content="應於期限內申報。", role="definition")
    penalty = _article(conn, "role-act", "50", content="違反第二十條者，處罰鍰。")
    conn.execute(
        "INSERT INTO sanction (sanction_iri, violated_iri, effect, amount_text)"
        " VALUES (%s, %s, 'fine', '處新臺幣六千元以下罰鍰')",
        (penalty, duty),
    )
    conn.commit()

    rows = conn.execute("SELECT violated_iri FROM sanction_on_ungroundable_norm").fetchall()
    assert [r[0] for r in rows] == [duty]


def test_penalty_on_a_duty_is_not_flagged(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    duty = _article(conn, "ok-act", "20", content="不得排放。", role="prohibition")
    penalty = _article(conn, "ok-act", "50", content="違反第二十條者，處罰鍰。")
    conn.execute(
        "INSERT INTO sanction (sanction_iri, violated_iri, effect, amount_text)"
        " VALUES (%s, %s, 'fine', '處新臺幣六千元以下罰鍰')",
        (penalty, duty),
    )
    conn.commit()

    rows = conn.execute("SELECT count(*) FROM sanction_on_ungroundable_norm").fetchone()
    assert rows is not None
    assert rows[0] == 0
