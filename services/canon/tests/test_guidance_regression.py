"""函釋入口回歸檢查（tools/guidance_regression.py）的規則與判定：不連資料庫、不打服務。"""

import ast
import copy
import json
import re
from pathlib import Path
from typing import Any

import guidance_regression as tool


def test_ngrams_stay_inside_a_run_of_han_characters() -> None:
    assert tool.ngrams("有關露天燃燒之認定") == {
        "有關露天", "關露天燃", "露天燃燒", "天燃燒之", "燃燒之認", "燒之認定",
    }  # fmt: skip
    # 標點、數字、英文把詞切斷：不跨過去湊出沒有人寫過的詞
    assert tool.ngrams("第3條，露天燃燒") == {"露天燃燒"}
    assert tool.ngrams("abc") == set()


def test_candidates_need_enough_letters_but_not_too_many() -> None:
    subjects = {n: "核釋露天燃燒之認定" for n in range(1, 4)} | {9: "只有一件的詞語條文"}
    subjects |= {100 + n: "常見的請查照函文" for n in range(tool.MAX_DOCS + 1)}

    found = tool.candidates(subjects)

    assert found["露天燃燒"] == [1, 2, 3]
    assert "只有一件" not in found  # 少於 3 件：可能只是巧合
    assert not [p for p in found if p in "常見的請查照函文"]  # 超過 30 件：太常見，不是鑑別詞


def test_the_expected_articles_are_the_ones_most_letters_point_to() -> None:
    anchors = {
        1: {("A", "32"), ("A", "67")},
        2: {("A", "32")},
        3: {("A", "32"), ("B", "5")},
        4: {("A", "32"), ("A", "67")},
        5: {("C", "9")},
    }

    # 5 件的 60% 是 3 件：只有第 32 條有 4 件；第 67 條 2 件不夠
    assert tool.consensus(anchors, [1, 2, 3, 4, 5]) == [("A", "32")]
    # 只有 2 件時，至少要 2 件都指到（一件的巧合不算）
    assert tool.consensus(anchors, [1, 4]) == [("A", "32"), ("A", "67")]
    assert tool.consensus(anchors, [3, 5]) == []


def test_the_penalty_chain_follows_punishers_of_punishers_up_to_the_depth() -> None:
    edges = {
        ("A", "32"): {("A", "67")},  # 第 67 條罰違反第 32 條
        ("A", "67"): {("A", "56")},  # 第 56 條罰不遵行第 67 條的命令
        ("A", "56"): {("A", "99")},
    }

    assert tool.closure(edges, [("A", "32")]) == [
        ("A", "32"),
        ("A", "56"),
        ("A", "67"),
        ("A", "99"),
    ]
    assert tool.closure(edges, [("A", "32")], depth=2) == [("A", "32"), ("A", "56"), ("A", "67")]
    assert tool.closure(edges, [("B", "1")]) == [("B", "1")]  # 沒有罰則：只有自己


def test_the_penalty_chain_stops_on_a_cycle() -> None:
    edges = {("A", "1"): {("A", "2")}, ("A", "2"): {("A", "1")}}

    assert tool.closure(edges, [("A", "1")]) == [("A", "1"), ("A", "2")]


def test_the_case_id_is_the_phrase_not_the_order() -> None:
    assert tool.case_id("露天燃燒") == tool.case_id("露天燃燒") != tool.case_id("繞流排放")


def test_found_in_counts_the_expected_articles_within_the_window() -> None:
    expected = [("A", "32"), ("A", "67"), ("A", "56")]
    returned = [("X", "1"), ("A", "32"), ("Y", "2"), ("A", "56")]

    assert tool.found_in(expected, returned) == [("A", "32"), ("A", "56")]
    assert tool.found_in(expected, returned, k=2) == [("A", "32")]


def test_measure_records_what_was_found_and_summary_rates() -> None:
    cases = [
        {"id": "g-1", "query": "甲", "expected": [["A", "32"], ["A", "67"]]},
        {"id": "g-2", "query": "乙", "expected": [["B", "5"]]},
    ]
    answers = {"甲": [("A", "32"), ("Z", "1")], "乙": []}

    run = {"cases": tool.measure(cases, lambda q: answers[q])}
    rates = tool.summary(run)

    assert run["cases"]["g-1"]["found"] == [["A", "32"]]
    assert rates["any_found"] == 0.5 and rates["all_found"] == 0.0
    assert rates["recall"] == 1 / 3


def test_a_case_that_lost_an_expected_article_is_a_regression() -> None:
    def run(**found: int) -> dict[str, dict[str, dict[str, list[list[str]]]]]:
        return {
            "cases": {c: {"found": [["A", str(i)] for i in range(n)]} for c, n in found.items()}
        }

    lost, gained = tool.regressions(run(a=2, b=1, c=0), run(a=1, b=1, c=1))

    assert (lost, gained) == (["a"], ["c"])


# --- 題目只能由規則產生：沒有手寫題目、沒有寫死的詞 -----------------------------------------------

_SOURCE = Path(tool.__file__)
_DATASETS = Path(__file__).parent.parent / "eval" / "datasets"
_QUESTION_SETS = sorted(_DATASETS.glob("guidance-regression-*.json"))


def _source() -> tool.Derivation:
    subjects = {n: "核釋露天燃燒之認定" for n in range(1, 5)} | {9: "無關的函釋主旨文字"}
    anchors = {1: {("A", "32")}, 2: {("A", "32")}, 3: {("A", "32")}, 4: {("A", "32")}}
    return tool.Derivation(
        subjects, anchors, {("A", "32"): {("A", "67")}, ("A", "67"): {("A", "56")}}
    )


def _not_in_articles(_: str) -> bool:
    return False


def _derived() -> dict[str, Any]:
    case = tool.derive_case("露天燃燒", _source(), _not_in_articles)
    assert case is not None
    return case


def test_a_case_is_whatever_the_rule_derives_from_the_data() -> None:
    case = _derived()

    assert set(case) == set(tool.CASE_FIELDS)
    assert case["anchored"] == [["A", "32"]]
    assert case["expected"] == [["A", "32"], ["A", "56"], ["A", "67"]]  # 錨定加上罰則鏈
    assert tool.shape_errors([case]) == []


def test_the_rule_rejects_phrases_the_data_does_not_support() -> None:
    source = _source()

    assert tool.derive_case("隨便寫的", source, _not_in_articles) is None  # 資料裡沒有這個詞
    assert tool.derive_case("認定", source, _not_in_articles) is None  # 不是 4 字
    assert tool.derive_case("無關的函", source, _not_in_articles) is None  # 只有 1 件
    # 條文逐字有這個詞：字面臂找得到，不是函釋入口的題
    assert tool.derive_case("露天燃燒", source, lambda _: True) is None


def test_a_hand_written_field_is_not_accepted() -> None:
    case = _derived() | {"note": "人工確認"}

    assert tool.shape_errors([case])


def test_a_made_up_phrase_or_id_is_not_accepted() -> None:
    made_up = _derived() | {"query": "隨便寫的"}

    assert any("題號" in e for e in tool.shape_errors([made_up]))
    assert any("題號" in e for e in tool.shape_errors([_derived() | {"id": "g-00000000"}]))


def test_expected_articles_must_include_the_anchored_ones() -> None:
    case = _derived() | {"expected": [["A", "67"]]}

    assert any("錨定" in e for e in tool.shape_errors([case]))


def test_verify_catches_an_edited_answer_that_still_has_a_valid_shape() -> None:
    edited = copy.deepcopy(_derived())
    edited["expected"].append(["A", "99"])  # 形狀合法，但規則對資料算不出來

    assert tool.shape_errors([edited]) == []
    assert tool.verify_cases([edited], _source(), _not_in_articles)
    assert tool.verify_cases([_derived()], _source(), _not_in_articles) == []


def test_verify_catches_a_case_the_rule_cannot_produce() -> None:
    forged = _derived() | {"query": "隨便寫的", "id": tool.case_id("隨便寫的")}

    assert any("產不出" in e for e in tool.verify_cases([forged], _source(), _not_in_articles))


def test_the_tool_has_no_hard_coded_four_character_phrase() -> None:
    """查詢詞只能來自資料：工具原始碼裡不准出現寫死的 4 個漢字的字串（文件字串除外）。"""
    tree = ast.parse(_SOURCE.read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    han = re.compile(r"[\u4e00-\u9fff]{4}")
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and han.fullmatch(node.value)
    ]

    assert literals == [], f"寫死的詞：{literals}"


def test_every_committed_question_set_has_only_derived_fields() -> None:
    assert _QUESTION_SETS, "eval/datasets/ 沒有題庫"
    for path in _QUESTION_SETS:
        cases = json.loads(path.read_text())["cases"]

        assert tool.shape_errors(cases) == [], path.name
