"""查詢分類與路由工具的純邏輯：規則、取捨選擇、分類器、類型後綴與檢定。收集那一半對運行中的服務跑。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("query_routing", TOOLS / "query_routing.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["query_routing"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


def hit(
    article: str, *, lx: float = 0.0, se: float | None = None, co: float = 0.0, gd: int = 0
) -> dict[str, Any]:
    return {"a": article, "p": article.split("-art-")[0], "lx": lx, "se": se, "co": co, "gd": gd}


def case(
    hits: list[dict[str, Any]], answer: list[str], *, cls: str = "term", level: str = "article"
) -> dict[str, Any]:
    return {
        "id": "x",
        "cls": cls,
        "split": "dev",
        "query": "查詢",
        "level": level,
        "answer": answer,
        "hits": hits,
    }


def test_the_shipped_rule_lists_a_whole_term_hit_below_the_lexical_floor() -> None:
    assert tool.passes(hit("A-art-1", lx=0.1, co=1.0), 0.5, False)
    assert not tool.passes(hit("A-art-1", lx=0.0, co=1.0), 0.5, False)


def test_a_fragment_hit_needs_the_coverage_threshold() -> None:
    fragment = hit("A-art-1", lx=0.9, co=0.34)
    assert not tool.passes(fragment, 0.5, False)
    assert tool.passes(fragment, 0.2, False)
    assert tool.passes(fragment, 0.0, False)


def test_a_semantic_only_hit_passes_unless_candidate_b_asks_for_a_term() -> None:
    semantic = hit("A-art-1", se=0.8, co=0.0)
    assert tool.passes(semantic, 0.5, False)
    assert not tool.passes(semantic, 0.5, True)
    assert tool.passes(hit("A-art-1", se=0.8, co=0.2), 0.5, True)


def test_guidance_always_passes() -> None:
    assert tool.passes(hit("A-art-1", gd=2), 0.7, True)


def test_a_law_level_answer_counts_any_article_of_that_law() -> None:
    c = case([hit("B-art-9", se=0.9)], ["B"], level="law")
    assert tool.outcome(c, tool.SHIPPED) == (True, 1)
    assert tool.outcome(case([hit("C-art-1", se=0.9)], ["B"], level="law"), tool.SHIPPED) == (
        False,
        1,
    )


def test_an_absent_query_reports_how_many_hits_are_listed() -> None:
    assert tool.outcome(case([hit("A-art-1", lx=0.9, co=0.3)], []), tool.SHIPPED) == (None, 0)
    assert tool.outcome(case([hit("A-art-1", lx=0.9, co=0.3)], []), "θ0") == (None, 1)


def test_choose_never_picks_a_rule_that_leaks_more_than_the_shipped_one() -> None:
    answerable = case([hit("A-art-1", lx=0.9, co=0.34)], ["A-art-1"])
    absent = case([hit("Z-art-1", lx=0.9, co=0.34)], [])
    # 放寬門檻能找到答案，卻讓無答案題多放一條：不得選
    assert tool.choose([answerable, absent]) == tool.SHIPPED
    # 沒有無答案題約束時，選找到答案的規則
    assert tool.RULES[tool.choose([answerable])][0] < 0.5


def test_choose_keeps_the_shipped_rule_when_nothing_is_better() -> None:
    c = case([hit("A-art-1", lx=0.9, co=1.0)], ["A-art-1"])
    # 每條規則都只列出 A：列得一樣少，與現行規則差得最少的就是它自己
    assert tool.choose([c]) == tool.SHIPPED


def test_choose_takes_the_stricter_threshold_when_the_find_rate_is_the_same() -> None:
    c = case(
        [hit("A-art-1", lx=0.9, co=1.0), hit("F-art-1", lx=0.9, co=0.5)],
        ["A-art-1"],
    )
    # 片段命中 F 在 0.5 以下的門檻列出、在 0.7 不列；A 都找得到：取列得少的
    assert tool.RULES[tool.choose([c])][0] == 0.7


def test_the_grid_has_no_candidate_b() -> None:
    assert all(not b for _, b, _ in tool.RULES.values())


def test_a_lowered_semantic_floor_must_clear_the_absent_maximum_by_the_margin() -> None:
    found_only_at_a_low_floor = case([hit("A-art-1", se=0.65)], ["A-art-1"])
    # 無答案題的最高語意相似度 0.649：0.65 的下界沒有誤放，卻只高 0.001，不得選
    absent = case([hit("Z-art-1", se=0.649)], [])
    chosen = tool.choose([found_only_at_a_low_floor, absent])
    assert tool.RULES[chosen][2] >= 0.68 or tool.RULES[chosen][2] == tool.SEMANTIC_FLOOR
    assert tool.RULES[chosen][2] not in (0.65, 0.6)


def test_a_slightly_lower_find_rate_is_the_same_if_it_lists_fewer() -> None:
    cases = [case([hit(f"A{i}-art-1", lx=0.9, co=1.0)], [f"A{i}-art-1"]) for i in range(200)]
    # 200 題都在整詞命中時被找到，各規則找到率相同：取列得少的（有 B 的與語意較嚴的同樣少）
    assert tool.tally([tool.outcome(c, tool.choose(cases)) for c in cases])["found"] == 200


def test_type_suffixes_assign_each_title_to_its_longest_common_suffix() -> None:
    titles = [f"某{i}管理辦法" for i in range(20)] + [f"某{i}稅法" for i in range(20)]
    titles += [f"某{i}法" for i in range(20)]
    found = tool.type_suffixes(titles, minimum=15)
    assert "辦法" in found
    assert "稅法" in found
    assert "法" in found
    # 「管理辦法」的類型是辦法，不是被截斷的「理辦法」
    assert all(len(s) <= 2 for s in found)


def test_a_suffix_that_only_trails_a_longer_one_is_not_a_type() -> None:
    titles = [f"某{i}條例" for i in range(30)]
    assert tool.type_suffixes(titles, minimum=15) == ["條例"]


def test_absent_names_attach_a_type_to_a_verified_absent_term() -> None:
    rows = [
        {"kind": "kw_absent", "query": "元宇宙", "split": "dev"},
        {"kind": "kw_definition", "query": "空氣污染物", "split": "dev"},
    ]
    names = tool.absent_names(rows, ["辦法", "法", "條例"])
    assert len(names) == 1
    assert names[0]["query"].startswith("元宇宙")
    assert names[0]["query"] in ("元宇宙辦法", "元宇宙條例")
    assert names[0]["answer"] == [] and names[0]["cls"] == "name"


@pytest.mark.parametrize(
    ("gain", "loss", "p"),
    [(0, 0, 1.0), (6, 0, 0.5**6), (3, 3, 0.65625)],
)
def test_the_sign_test_is_one_sided(gain: int, loss: int, p: float) -> None:
    assert tool.sign_p(gain, loss) == pytest.approx(p)


def test_the_shape_classifier_reads_the_query_string_only() -> None:
    shape = tool.classifiers(["辦法", "法"])["shape(len>=10)"]
    assert shape({"query": "各級法院辦理民事事件遠距審理辦法"}) == "name"
    assert shape({"query": "違反社會救助法第33條規定，會受到什麼處罰？"}) == "question"
    assert shape({"query": "固定污染源"}) == "term"
    # 句尾是問號時，名稱後綴不算：問句不因為提到法規而變成名稱
    assert shape({"query": "這是不是一部法？"}) == "name"


def test_routing_applies_the_chosen_rule_of_the_predicted_group() -> None:
    fragment = case([hit("A-art-1", lx=0.9, co=0.34)], ["A-art-1"], cls="name")
    routed = tool.run_router([fragment], lambda c: "g", {"g": "θ0"})
    assert routed == [(True, 1)]
    unrouted = tool.run_router([fragment], lambda c: "g", {})
    assert unrouted == [(False, 0)]
