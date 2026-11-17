"""函釋入口回歸檢查（tools/guidance_regression.py）的規則與判定：不連資料庫、不打服務。"""

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
