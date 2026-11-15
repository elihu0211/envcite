"""線上實測的純邏輯：預測、比對與取樣。對線上的呼叫由 `live-check` 在有 API key 的人手上跑。"""

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

CLASSIFY = tool.classifiers(["法", "條例", "標準"])[tool.DEPLOYED_CLASSIFIER]


def hit(article: str, lx: float = 0.0, se: float | None = None, co: float = 0.0) -> dict[str, Any]:
    return {"a": article, "p": article.split("-art-")[0], "lx": lx, "se": se, "co": co, "gd": 0}


def case(
    query: str, hits: list[dict[str, Any]], kind: str = "k", ident: str = "x"
) -> dict[str, Any]:
    return {
        "id": ident,
        "kind": kind,
        "cls": "term",
        "split": "s",
        "query": query,
        "level": "article",
        "answer": [],
        "hits": hits,
    }


def test_the_prediction_applies_the_rule_of_the_queries_own_type() -> None:
    hits = [hit("A-art-1", lx=0.8, co=0.6), hit("B-art-1", se=0.72)]
    # 關鍵字：覆蓋率 0.6 < 0.7、語意 0.72 < 0.74，兩條都不列
    assert tool.predicted_ids(case("露天燃燒", hits), CLASSIFY) == []
    # 問句：覆蓋率 0.6 ≥ 0.5、語意 0.72 ≥ 0.68，兩條都列
    assert tool.predicted_ids(case("露天燃燒是什麼？", hits), CLASSIFY) == ["A-art-1", "B-art-1"]
    # 名稱：覆蓋率 0.6 ≥ 0.5 列；語意 0.72 ≥ 共用 0.71 也列
    assert tool.predicted_ids(case("噪音管制標準", hits), CLASSIFY) == ["A-art-1", "B-art-1"]


def test_the_prediction_keeps_the_order_and_stops_at_ten() -> None:
    hits = [hit(f"A-art-{i}", lx=0.9, co=1.0) for i in range(14)]
    assert tool.predicted_ids(case("露天燃燒", hits), CLASSIFY) == [f"A-art-{i}" for i in range(10)]


def test_the_live_ids_are_written_like_the_offline_ones() -> None:
    live = [
        {"identifier": {"localId": "O0020001", "idScheme": "moj_pcode"}, "number": "5-1"},
        {"identifier": {"localId": "G0380001", "idScheme": "moj_pcode"}, "number": "23"},
    ]
    assert tool.live_ids(live) == ["O0020001-art-5-1", "G0380001-art-23"]


def test_a_comparison_tells_identical_same_set_and_different_apart() -> None:
    assert tool.compare_lists(["a", "b"], ["a", "b"]) == "identical"
    assert tool.compare_lists(["b", "a"], ["a", "b"]) == "same-set"
    assert tool.compare_lists(["a"], ["a", "b"]) == "differs"
    assert tool.compare_lists([], []) == "identical"


def test_the_sample_covers_every_kind_and_does_not_depend_on_the_input_order() -> None:
    cases = [case(f"q{i}", [], kind=f"k{i % 3}", ident=f"id{i}") for i in range(30)]
    picked = tool.stratified_sample(cases, 6)
    assert {c["kind"] for c in picked} == {"k0", "k1", "k2"}
    assert [c["id"] for c in picked] == [c["id"] for c in tool.stratified_sample(cases[::-1], 6)]
    assert len(tool.stratified_sample(cases, 100)) == 30


def test_the_report_lists_the_differences_by_kind() -> None:
    result = {
        "total": 2,
        "summary": {"identical": 1, "differs": 1},
        "rows": [
            {"kind": "a", "query": "q1", "live": 1, "predicted": 1, "match": "identical"},
            {"kind": "b", "query": "q2", "live": 0, "predicted": 2, "match": "differs"},
        ],
    }
    text = tool.render_live(result)
    assert "| a | 1 | 0 | 0 |" in text and "| b | 0 | 0 | 1 |" in text
    assert "q2" in text


def test_a_pasted_key_loses_its_whitespace_and_a_stray_bearer_prefix() -> None:
    assert tool.clean_key("  abc123\n") == "abc123"
    assert tool.clean_key("Bearer abc123") == "abc123"
    assert tool.clean_key("abc123") == "abc123"


def test_the_terminals_paste_markers_and_control_characters_are_removed_from_a_key() -> None:
    assert tool.clean_key("\x1b[200~abc123\x1b[201~") == "abc123"
    assert tool.clean_key("abc​123\r\n") == "abc123"
    assert tool.clean_key("Bearer \x1b[200~abc123\x1b[201~\n") == "abc123"
    assert tool.clean_key("") == ""


def test_a_well_formed_key_passes_and_anything_else_is_explained_without_echoing_it() -> None:
    good = "sk_live_" + "0123456789ab" + "_" + "A" * 43
    assert len(good) == tool.KEY_LENGTH and tool.key_problem(good) is None
    for bad in ("", "thu-env", "sk_live_short", good[:-1], good.replace("sk_live_", "sk_test_")):
        message = tool.key_problem(bad)
        assert message and str(len(bad)) in message
        assert bad == "" or bad not in message


def test_an_http_error_is_one_sentence_that_names_the_cause_and_never_the_key() -> None:
    assert "已撤銷或已過期" in tool.explain_http_error(401)
    assert "方案" in tool.explain_http_error(402)
    assert "ontology:read" in tool.explain_http_error(403)
    assert tool.explain_http_error(500).startswith("線上回 HTTP 500")
