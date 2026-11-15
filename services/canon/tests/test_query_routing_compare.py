"""兩條規則逐題配對的比較：增減、誤放與列出條數。"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("query_routing", TOOLS / "query_routing.py")
assert spec and spec.loader
tool = importlib.util.module_from_spec(spec)
sys.modules["query_routing"] = tool
spec.loader.exec_module(tool)


def case(kind: str, hits: list[dict[str, Any]], answer: list[str]) -> dict[str, Any]:
    return {
        "id": kind,
        "kind": kind,
        "cls": "term",
        "split": "fresh",
        "query": "查詢",
        "level": "article",
        "answer": answer,
        "hits": hits,
    }


def hit(article: str, lx: float, co: float) -> dict[str, Any]:
    return {"a": article, "p": article.split("-art-")[0], "lx": lx, "se": None, "co": co, "gd": 0}


def test_a_stricter_threshold_lists_fewer_and_the_pairing_counts_each_direction() -> None:
    cases = [
        # 部分命中（覆蓋率 0.6）：θ0.5 列、θ0.7 不列
        case("fresh_partial", [hit("A-art-1", 0.9, 0.6)], ["A-art-1"]),
        # 整詞命中：兩條規則都列
        case("fresh_definition", [hit("B-art-1", 0.9, 1.0)], ["B-art-1"]),
        # 無答案：片段命中在 θ0.5 誤放、θ0.7 不誤放
        case("fresh_absent", [hit("Z-art-1", 0.9, 0.6)], []),
    ]
    result = tool.compare_rules(cases, "θ0.5", "θ0.7")

    assert (result["fresh_partial"]["gain"], result["fresh_partial"]["loss"]) == (0, 1)
    assert (result["fresh_definition"]["gain"], result["fresh_definition"]["loss"]) == (0, 0)
    assert (result["fresh_absent"]["leak_less"], result["fresh_absent"]["leak_more"]) == (1, 0)
    assert result["fresh_partial"]["shorter"] == 1


def test_the_rendered_table_names_both_rules() -> None:
    cases = [case("fresh_definition", [hit("B-art-1", 0.9, 1.0)], ["B-art-1"])]
    text = tool.render_compare(tool.compare_rules(cases, "θ0.5", "θ0.7"), "θ0.5", "θ0.7")
    assert "θ0.5 → θ0.7" in text and "fresh_definition" in text
