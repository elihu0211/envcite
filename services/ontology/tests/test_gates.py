"""The three deterministic gates, and reading numbers and citations out of the text (envcite-
docs 02-06 §3.3)."""

from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.annotate import load
from envcite_ontology.verification import (
    ExecutableGate,
    NumbersGate,
    ReferencesGate,
    settle,
)
from envcite_ontology.verification.gates import amounts, citations, cn_number
from envcite_ontology.verification.gates.cn_number import qualified_numbers
from envcite_ontology.verification.gates.proviso_gate import ProvisoGate, provisos
from tests.reasoning_support import _plain_duty, _scope, _with
from tests.test_annotation import LAW, _doc, _limit
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import (
    DETERMINISTIC,
    Conn,
    _review,
    _water,
    _water_with,
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

    wrong = _water_with(conn, "p40_1_fine", max=2000000).ids["p40_1_fine"]
    result = gate.check(conn, wrong)
    assert result is not None and result.verdict == "fail"


def test_numbers_gate_fails_an_amount_the_text_does_not_have(conn: Conn, sample: None) -> None:
    ids = _water_with(conn, "p40_1_fine", amount_text="新臺幣一萬元罰鍰").ids

    result = NumbersGate().check(conn, ids["p40_1_fine"])
    assert result is not None and result.verdict == "fail"


def test_references_gate_requires_the_punished_provision_to_be_the_cited_one(
    conn: Conn, sample: None
) -> None:
    ids = _water(conn, sample).ids
    gate = ReferencesGate()
    conn.execute(
        "UPDATE annotation.claim SET confirm_state = 'confirmed' WHERE id = %s", (ids["w7_1"],)
    )

    ok = gate.check(conn, ids["p40_1_fine"])
    assert ok is not None and ok.verdict == "pass"
    assert ok.evidence["unresolved"] == ["art_8"]  # 第八條不在語料裡：只記，不算錯

    # 第七條第二項：原文引用的是第一項
    wrong = _water_with(conn, "p40_1_fine", punishes="w7_2b").ids["p40_1_fine"]
    bad = gate.check(conn, wrong)
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
        "kind": "sanction",
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


def _belonging_duty() -> dict[str, Any]:
    """義務：排放者的行業須屬於「受管制事業」（概念值要件）。"""
    duty = _plain_duty()
    duty["conditions"][1] = {
        "role": "duty",
        "clause": 1,
        "concept": "industry",
        "comparator": "eq",
        "value_concept": "regulated",
    }
    return duty


def test_executable_gate_runs_a_condition_on_a_concept_value(conn: Conn, tmp_path: Path) -> None:
    """概念值要件成立取該概念本身、不成立取不屬於任何用語的值，不靠任何定義就能翻轉結論。"""
    _first(conn, tmp_path, _law_v1())
    doc = _doc(_belonging_duty(), review="extracted")
    doc["concepts"] = [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "industry", "label": "行業"},
        {"code": "regulated", "label": "受管制事業"},
    ]
    loaded = load(conn, doc)

    result = ExecutableGate().check(conn, loaded.ids["duty"])

    assert result is not None and result.verdict == "pass"
    assert {c["case"] for c in result.evidence["cases"]} >= {"duty holds", "duty broken"}


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("年滿十八歲以上者", [(18, "以上")]),
        ("排放水量五十立方公尺以下", [(50, "以下")]),
        ("三十日以內改善", [(30, "以內")]),
        ("未滿十四歲人之行為不罰", [(14, "未滿")]),
        ("未達二百立方公尺", [(200, "未達")]),
        ("超過三十日者", []),  # 「超過」「逾」沒有查到明文的含本數規則，不判定
        ("處六萬元以上二千萬元以下罰鍰", [(60000, "以上"), (20000000, "以下")]),
    ],
)
def test_comparison_words_are_read_with_their_numbers(
    text: str, expected: list[tuple[int, str]]
) -> None:
    assert qualified_numbers(text) == expected


def _age_rule(conn: Conn, tmp_path: Path, comparator: str, *, negated: bool = False) -> int:
    """原文「甲一十八歲以上者」，標註一個年齡要件。"""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一十八歲以上者", "乙一")))
    duty = _plain_duty("甲一十八歲以上者")
    duty["conditions"] = [
        _scope("discharging"),
        {
            "role": "duty",
            "clause": 1,
            "concept": "age",
            "comparator": comparator,
            "threshold": 18,
            "unit": "歲",
            "negated": negated,
        },
    ]
    return load(conn, _with(_doc(duty), "age")).ids["duty"]


@pytest.mark.parametrize(
    ("comparator", "negated", "verdict"),
    [
        ("ge", False, "pass"),  # 以上：連本數
        ("lt", False, "pass"),  # 要件描述違反的那一側：補集
        ("gt", False, "fail"),  # 把本數排除了
        ("le", False, "fail"),  # 方向反了
        ("lt", True, "pass"),  # 非（小於）即大於等於
        ("ge", True, "pass"),  # 非（大於等於）即小於，是補集
        ("gt", True, "fail"),  # 非（大於）即小於等於：方向反了
    ],
)
def test_numbers_gate_checks_whether_the_boundary_is_included(
    conn: Conn, tmp_path: Path, comparator: str, negated: bool, verdict: str
) -> None:
    sid = _age_rule(conn, tmp_path, comparator, negated=negated)

    result = NumbersGate().check(conn, sid)

    assert result is not None and result.verdict == verdict


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("違反者處罰鍰。但情節輕微者，得免罰。", ["但情節輕微者，得免罰。"]),
        ("事業應申報；但經核准者，不在此限。", ["但經核准者，不在此限。"]),
        ("未滿十八歲者，不在此限。", ["未滿十八歲者，不在此限"]),
        ("事業應申報。但法律另有規定者，從其規定。", []),  # 保留規定，不是例外
        ("事業應申報，違反者處罰鍰。", []),
    ],
)
def test_provisos_are_read_from_the_text(text: str, expected: list[str]) -> None:
    assert provisos(text) == expected


PROVISO_TEXT = "甲一。但乙者，不在此限。"


def _with_proviso_text(conn: Conn, tmp_path: Path, *, with_exception: bool) -> int:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(PROVISO_TEXT, "乙一")))
    statements = [_plain_duty(PROVISO_TEXT)]
    if with_exception:
        statements.append(
            {
                "id": "proviso",
                "law": LAW,
                "path": "art_1",
                "text": PROVISO_TEXT,
                "kind": "constitutive",
                "exception_of": "duty",
                "conditions": [_scope("excused")],
            }
        )
    return load(conn, _with(_doc(*statements), "excused")).ids["duty"]


def test_a_proviso_in_the_text_needs_an_exception_statement(conn: Conn, tmp_path: Path) -> None:
    """往返驗證看不到原文：漏掉的但書在描述裡一起消失。這個閘門直接讀原文。"""
    sid = _with_proviso_text(conn, tmp_path, with_exception=False)

    result = ProvisoGate().check(conn, sid)

    assert result is not None and result.verdict == "inconclusive"
    assert result.evidence["provisos"] == ["但乙者，不在此限。"]


def test_a_proviso_with_its_exception_statement_passes(conn: Conn, tmp_path: Path) -> None:
    sid = _with_proviso_text(conn, tmp_path, with_exception=True)

    result = ProvisoGate().check(conn, sid)

    assert result is not None and result.verdict == "pass"


def test_text_without_a_proviso_does_not_apply(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    sid = load(conn, _doc(_plain_duty())).ids["duty"]

    assert ProvisoGate().check(conn, sid) is None
