"""The three deterministic gates, and reading numbers and citations out of the text (env-law-
docs 02-05 §5.9)."""

from pathlib import Path

import pytest

from env_law_ontology.annotate import load
from env_law_ontology.verification import (
    ExecutableGate,
    NumbersGate,
    ReferencesGate,
    settle,
)
from env_law_ontology.verification.gates import amounts, citations, cn_number
from tests.reasoning_support import _plain_duty, _scope, _with
from tests.test_annotation import LAW, _doc, _limit
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    DETERMINISTIC,
    Conn,
    _review,
    _water,
)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("六萬", 60000),
        ("二千萬", 20000000),
        ("一百", 100),
        ("六十萬", 600000),
        ("六千", 6000),
        ("一百零五", 105),
        ("十", 10),
        ("1,000", 1000),
        ("三億二千萬", 320000000),
    ],
)
def test_chinese_numbers(text: str, value: int) -> None:
    assert cn_number(text) == value


def test_the_amounts_and_their_bounds_are_read_from_a_penalty_sentence() -> None:
    assert amounts("處新臺幣六萬元以上二千萬元以下罰鍰，並通知限期改善") == [
        (60000, "以上"),
        (20000000, "以下"),
    ]


def test_citations_resolve_to_provision_paths() -> None:
    assert citations("違反第七條第一項或第八條規定者") == ["art_7/para_1", "art_8"]
    assert citations("依第五條之一第二項辦理") == ["art_5-1/para_2"]


def test_numbers_gate_checks_the_amounts_against_the_text(conn: Conn, sample: None) -> None:
    ids = _water(conn, sample).ids
    gate = NumbersGate()

    assert gate.check(conn, ids["p40_1_fine"]).verdict == "pass"  # type: ignore[union-attr]
    assert gate.check(conn, ids["p40_2_fine"]).verdict == "pass"  # type: ignore[union-attr]
    assert gate.check(conn, ids["w7_1"]) is None  # 沒有罰則、沒有帶單位的門檻值

    conn.execute(
        "UPDATE annotation.sanction SET max_amount = 2000000 WHERE statement_id = %s",
        (ids["p40_1_fine"],),
    )
    result = gate.check(conn, ids["p40_1_fine"])
    assert result is not None and result.verdict == "fail"


def test_numbers_gate_fails_an_amount_the_text_does_not_have(conn: Conn, sample: None) -> None:
    ids = _water(conn, sample).ids
    conn.execute(
        "UPDATE annotation.sanction SET amount_text = '新臺幣一萬元罰鍰' WHERE statement_id = %s",
        (ids["p40_1_fine"],),
    )

    result = NumbersGate().check(conn, ids["p40_1_fine"])
    assert result is not None and result.verdict == "fail"


def test_references_gate_requires_the_punished_provision_to_be_the_cited_one(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids
    gate = ReferencesGate()
    conn.execute(
        "UPDATE annotation.statement SET confirm_state = 'confirmed' WHERE id = %s", (ids["w7_1"],)
    )

    ok = gate.check(conn, ids["p40_1_fine"])
    assert ok is not None and ok.verdict == "pass"
    assert ok.evidence["unresolved"] == ["art_8"]  # 第八條不在語料裡：只記，不算錯

    conn.execute(
        "UPDATE annotation.sanction SET punishes_id = %s WHERE statement_id = %s",
        (ids["w7_2b"], ids["p40_1_fine"]),  # 第七條第二項：原文引用的是第一項
    )
    bad = gate.check(conn, ids["p40_1_fine"])
    assert bad is not None and bad.verdict == "fail"
    assert gate.check(conn, ids["w7_1"]) is None


@pytest.mark.parametrize("deontic", ["obligation", "prohibition", "permission"])
def test_executable_gate_follows_the_deontic_polarity(
    conn: Conn, tmp_path: Path, deontic: str
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    duty = _plain_duty()
    duty["deontic"] = deontic
    loaded = load(conn, _doc(duty, review="extracted"))

    result = ExecutableGate().check(conn, loaded.ids["duty"])

    assert result is not None and result.verdict == "pass"
    assert {c["case"] for c in result.evidence["cases"]} >= {"duty holds", "duty broken"}


def test_executable_gate_checks_a_penalty_only_while_the_breach_stands(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    penalty = {
        "id": "fine",
        "law": LAW,
        "path": "art_2",
        "text": "乙一",
        "kind": "penalty",
        "conditions": [_scope("serious")],
        "sanction": {"punishes": "duty", "effect": "fine", "min": 1, "max": 2},
    }
    loaded = load(conn, _with(_doc(_plain_duty(), penalty), "serious"))

    result = ExecutableGate().check(conn, loaded.ids["fine"])

    assert result is not None and result.verdict == "pass"
    assert {c["case"] for c in result.evidence["cases"]} == {
        "scope holds",
        "serious not met",
        "scope unknown",
    }


def test_executable_gate_is_not_applicable_outside_obligations(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1())
    loaded = load(conn, _doc(_limit("乙一", 100), review="extracted"))

    assert ExecutableGate().check(conn, loaded.ids["limit100"]) is None


def test_executable_gate_waits_for_the_limit_it_depends_on(conn: Conn, tmp_path: Path) -> None:
    """委任的限值所依的陳述還沒被接受時，門檻值解析不到：無法判定，不是失敗。"""
    from tests.test_annotation import _duty

    limit_text = "化學需氧量限值：每公升一百毫克"
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", limit_text)))
    loaded = load(conn, _doc(_duty(), _limit(limit_text, 100), review="extracted"))

    waiting = ExecutableGate().check(conn, loaded.ids["duty"])
    assert waiting is not None and waiting.verdict == "inconclusive"

    result = settle(conn, DETERMINISTIC)
    assert result.confirmed == 2  # 限值先被接受，委任的義務在下一輪才可判定
    assert result.rounds >= 2
    assert _review(conn, loaded.ids["duty"]) == "confirmed"


def test_executable_gate_does_not_guess_when_it_cannot_run_the_conditions(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1())
    twice = _plain_duty()
    twice["conditions"].append(
        {"role": "scope", "clause": 1, "concept": "cod", "comparator": "ge", "threshold": 5}
    )
    loaded = load(conn, _doc(twice, review="extracted"))

    result = ExecutableGate().check(conn, loaded.ids["duty"])
    assert result is not None and result.verdict == "inconclusive"
