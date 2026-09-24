"""Hybrid retrieval: lexical, dense, and the filters that run before both.

What these tests are really about is the failure mode hybrid retrieval has and
flat retrieval does not: **a query that returns a plausible list with the right
answer missing**. Nothing raises, the ranking looks sensible, and the only way to
notice is to already know what should have been there. So the cases here are
mostly about exclusion — a version not in force, a vector from another model,
lexemes from another segmenter, a statute outside the seeded subgraph — rather
than about ranking quality.

Segmentation happens outside the database (0010), so these tests hand it words,
exactly as the importer and the query path will.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import psycopg
import pytest

BASE = "https://env.law/eli/tw/law"
DIM = 1024
MODEL = "test-embed-v1"
TOKENIZER = "test-seg-v1"


def _as_float(value: object) -> float:
    """Rows come back as tuple[object, ...]; assert the shape before comparing.

    Decimal is in here because `score` is numeric — psycopg maps that to Decimal,
    not float, and the two do not compare with int in the same way.
    """
    assert isinstance(value, int | float | Decimal), f"expected a number, got {type(value)}"
    return float(value)


def _vec(hot: int) -> str:
    """A unit vector with a single non-zero position, so distances are predictable."""
    values = ["0"] * DIM
    values[hot] = "1"
    return "[" + ",".join(values) + "]"


@pytest.fixture
def corpus(conn: psycopg.Connection[tuple[object, ...]]) -> Iterator[dict[str, str]]:
    """Two statutes, one of them authorised by the other, three articles in force.

    Each article carries its text and, separately, that text already segmented —
    which is what the database indexes. air-act §20 is the obvious answer to a
    query about emissions; air-act §21 is about reporting; other-act §5 is outside
    the subgraph seeded from air-act.
    """
    made: dict[str, str] = {}
    for slug, authorised in (("air-act", None), ("air-rule", "air-act"), ("other-act", None)):
        conn.execute(
            "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')",
            (slug, f"{BASE}/{slug}"),
        )
        if authorised is not None:
            conn.execute(
                "INSERT INTO norm_relation (source_id, target_id, kind)"
                " VALUES (%s, %s, 'authorizes')",
                (authorised, slug),
            )

    articles = [
        (
            "air-act",
            "20",
            "公私場所不得排放空氣污染物超過排放標準。",
            "公私場所 不得 排放 空氣污染物 超過 排放標準",
            0,
        ),
        ("air-act", "21", "公私場所應每季申報排放量。", "公私場所 應 每季 申報 排放量", 1),
        ("other-act", "5", "本法所稱主管機關如下。", "本法 所稱 主管機關 如下", 2),
    ]
    for slug, number, content, segmented, hot in articles:
        expression = f"{BASE}/{slug}/v1"
        conn.execute(
            "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
            " VALUES (%s, %s, '測試法', 'zh-hant', DATE '2020-01-01') ON CONFLICT DO NOTHING",
            (expression, slug),
        )
        article = f"{expression}/art_{number}"
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri, content,"
            "  content_tsv, tokenizer, embedding, embedding_model)"
            " VALUES (%s, %s, %s, %s, %s, %s,"
            "  to_tsvector('simple', %s), %s, %s::article_vector, %s)",
            (
                article,
                slug,
                number,
                expression,
                article,
                content,
                segmented,
                TOKENIZER,
                _vec(hot),
                MODEL,
            ),
        )
        made[f"{slug}-{number}"] = article
    conn.commit()
    yield made


def _search(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    terms: str = "排放",
    vector: str | None = None,
    model: str = MODEL,
    tokenizer: str = TOKENIZER,
    as_of: str = "2024-01-01",
    seed: str | None = None,
) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT iri, score, lexical_rank, dense_rank, lexical_score, dense_distance"
        " FROM search_articles("
        " p_query_terms => %s, p_query_vector => %s::article_vector,"
        " p_embedding_model => %s, p_tokenizer => %s,"
        " p_as_of => %s::date, p_seed => %s)",
        (terms, vector or _vec(0), model, tokenizer, as_of, seed),
    ).fetchall()


# ---------------------------------------------------------------- extensions


def test_vector_is_the_only_search_extension(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Since 0010 the lexical arm is built in, so `vector` is the whole list.

    pg_search must be gone, not merely unused: an installed extension is one the
    deployment still has to mount an image for.
    """
    rows = conn.execute(
        "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_search') ORDER BY extname"
    ).fetchall()
    assert [r[0] for r in rows] == ["vector"]


# ---------------------------------------------------------------- tsquery


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ("排放", "'排放'"),
        ("排放 申報", "'排放' | '申報'"),
        ("  排放   申報  ", "'排放' | '申報'"),
    ],
)
def test_terms_become_an_or_query(
    conn: psycopg.Connection[tuple[object, ...]], terms: str, expected: str
) -> None:
    """OR, not AND: the lexical arm feeds RRF, where a broad set with a good
    ordering beats a narrow set. Requiring every term would make the arm return
    nothing for most real questions, which silently reduces hybrid retrieval to
    dense-only."""
    row = conn.execute("SELECT terms_to_tsquery(%s)::text", (terms,)).fetchone()
    assert row is not None
    assert row[0] == expected


def test_empty_terms_match_nothing(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    row = conn.execute("SELECT terms_to_tsquery('')::text").fetchone()
    assert row is not None
    assert row[0] == ""


def test_query_text_is_escaped_not_parsed(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """User text reaching a tsquery parser is a way to turn a question into a
    syntax error, or worse. plainto_tsquery escapes; to_tsquery would parse."""
    row = conn.execute("SELECT terms_to_tsquery(%s)::text", ("排放 & | ! ( )",)).fetchone()
    assert row is not None
    assert row[0] == "'排放'"


# ---------------------------------------------------------------- constraints


def test_a_vector_must_name_its_model(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Vectors from two models are not comparable, and the distance between them
    is a number with no meaning that ranks results perfectly plausibly."""
    expression = _bare_law(conn, "nomodel-act")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri, content, embedding)"
            " VALUES (%s, 'nomodel-act', '1', %s, %s, '某條文。', %s::article_vector)",
            (f"{expression}/art_1", expression, f"{expression}/art_1", _vec(0)),
        )


def test_a_tsvector_must_name_its_tokenizer(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """Lexemes produced by a different segmenter are not the same lexemes."""
    expression = _bare_law(conn, "notok-act")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri, content, content_tsv)"
            " VALUES (%s, 'notok-act', '1', %s, %s, '某條文。', to_tsvector('simple', '某 條文'))",
            (f"{expression}/art_1", expression, f"{expression}/art_1"),
        )


def test_a_vector_needs_text_behind_it(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    expression = _bare_law(conn, "notext-act")

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO subdivision"
            " (iri, resource_id, number, expression_iri, article_iri,"
            "  embedding, embedding_model)"
            " VALUES (%s, 'notext-act', '1', %s, %s, %s::article_vector, %s)",
            (f"{expression}/art_1", expression, f"{expression}/art_1", _vec(0), MODEL),
        )


def test_dimension_is_enforced_by_the_type(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """A model of another width cannot be written in by accident."""
    with pytest.raises(psycopg.errors.DataException):
        conn.execute("SELECT '[1,2,3]'::article_vector")


def _bare_law(conn: psycopg.Connection[tuple[object, ...]], slug: str) -> str:
    """One law with one expression and no articles. Returns the expression iri."""
    conn.execute(
        "INSERT INTO legal_resource (id_local, iri, kind) VALUES (%s, %s, 'statute')",
        (slug, f"{BASE}/{slug}"),
    )
    expression = f"{BASE}/{slug}/v1"
    conn.execute(
        "INSERT INTO legal_expression (iri, resource_id, title, language, valid_from)"
        " VALUES (%s, %s, '測試法', 'zh-hant', DATE '2020-01-01')",
        (expression, slug),
    )
    return expression


# ---------------------------------------------------------------- retrieval


def test_both_arms_find_the_obvious_article(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    rows = _search(conn)
    assert rows, "no results at all"
    assert rows[0][0] == corpus["air-act-20"]
    # Ranked by both arms, which is what fusion is for.
    assert rows[0][2] is not None
    assert rows[0][3] is not None
    # And the raw signals come back, because the fused score cannot answer
    # "is anything here actually relevant" — it only reflects positions.
    assert _as_float(rows[0][4]) > 0
    assert rows[0][5] == pytest.approx(0.0, abs=1e-6)


def test_a_version_not_in_force_is_not_returned(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """Before any version exists, the answer is nothing — not the current text."""
    assert _search(conn, as_of="2010-01-01") == []


def test_vectors_from_another_model_do_not_participate(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """Asking with a different model must not silently rank against foreign vectors."""
    rows = _search(conn, model="some-other-model")
    assert all(r[3] is None for r in rows), "a foreign-model vector was ranked"


def test_lexemes_from_another_tokenizer_do_not_participate(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """A tokenizer mismatch returns fewer rows, not wrong ones — harder to notice."""
    rows = _search(conn, tokenizer="some-other-segmenter")
    assert all(r[2] is None for r in rows), "a foreign-tokenizer row was ranked lexically"


def test_seed_restricts_to_the_corpus_subgraph(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """Seeded from air-act, other-act is out of scope even if its text matches."""
    found = {r[0] for r in _search(conn, terms="主管機關", seed="air-act")}
    assert corpus["other-act-5"] not in found


def test_without_a_seed_the_whole_corpus_is_in_scope(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    found = {r[0] for r in _search(conn, terms="主管機關")}
    assert corpus["other-act-5"] in found


def test_the_lexical_arm_matches_words_not_characters(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """Asserted on the lexical arm alone.

    The dense arm returns its nearest neighbours however far away they are, so
    every article reachable by the filters appears in the fused result — at a low
    rank, but present. Checking the fused set would test nothing about matching.
    """
    lexical_hits = {r[0] for r in _search(conn, terms="申報") if r[2] is not None}
    assert lexical_hits == {corpus["air-act-21"]}


def test_a_word_shared_by_two_articles_matches_both(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    lexical_hits = {r[0] for r in _search(conn, terms="公私場所") if r[2] is not None}
    assert lexical_hits == {corpus["air-act-20"], corpus["air-act-21"]}


def test_the_fused_score_is_not_a_relevance_measure(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """A query with no relevant article still returns a top result with a score.

    RRF uses only positions, so the best of a bad set scores exactly as the best
    of a good set would. This is why the raw signals are returned: deciding there
    is no answer needs the distance, not the rank.
    """
    rows = _search(conn, terms="完全無關的詞", vector=_vec(999))
    assert rows, "the dense arm returns nearest neighbours regardless of distance"
    assert _as_float(rows[0][1]) > 0, "and they carry a perfectly ordinary fused score"
    # The distance is what gives it away.
    assert rows[0][5] == pytest.approx(1.0, abs=1e-6)


def test_max_distance_excludes_far_neighbours(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """The ceiling exists but has no default: any figure is model-conditioned."""
    rows = conn.execute(
        "SELECT iri FROM search_articles("
        " p_query_terms => '完全無關的詞', p_query_vector => %s::article_vector,"
        " p_embedding_model => %s, p_tokenizer => %s,"
        " p_as_of => DATE '2024-01-01', p_max_distance => 0.5)",
        (_vec(999), MODEL, TOKENIZER),
    ).fetchall()
    assert rows == []


# ---------------------------------------------------------------- gaps


def test_article_without_a_vector_is_enumerable(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The dense arm cannot see it, and the query returns a plausible list without it."""
    expression = _bare_law(conn, "novec-act")
    article = f"{expression}/art_1"
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, 'novec-act', '1', %s, %s, '某條文。')",
        (article, expression, article),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT iri FROM article_missing_embedding WHERE resource_id = 'novec-act'"
    ).fetchall()
    assert [r[0] for r in rows] == [article]


def test_article_without_a_tsvector_is_enumerable(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    expression = _bare_law(conn, "notsv-act")
    article = f"{expression}/art_1"
    conn.execute(
        "INSERT INTO subdivision"
        " (iri, resource_id, number, expression_iri, article_iri, content)"
        " VALUES (%s, 'notsv-act', '1', %s, %s, '某條文。')",
        (article, expression, article),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT iri FROM article_missing_tsv WHERE resource_id = 'notsv-act'"
    ).fetchall()
    assert [r[0] for r in rows] == [article]


def test_more_than_one_model_in_the_corpus_is_visible(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    """Two models means retrieval is partitioned: each query sees only its own half."""
    conn.execute(
        "UPDATE subdivision SET embedding_model = 'another-model' WHERE iri = %s",
        (corpus["other-act-5"],),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT embedding_model, articles FROM embedding_model_in_use ORDER BY embedding_model"
    ).fetchall()
    assert [r[0] for r in rows] == ["another-model", MODEL]


def test_more_than_one_tokenizer_in_the_corpus_is_visible(
    conn: psycopg.Connection[tuple[object, ...]], corpus: dict[str, str]
) -> None:
    conn.execute(
        "UPDATE subdivision SET tokenizer = 'another-segmenter' WHERE iri = %s",
        (corpus["other-act-5"],),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT tokenizer, articles FROM tokenizer_in_use ORDER BY tokenizer"
    ).fetchall()
    assert [r[0] for r in rows] == ["another-segmenter", TOKENIZER]
