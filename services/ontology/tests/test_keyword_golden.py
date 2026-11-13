"""關鍵字題庫工具的純邏輯：定義行的抽取、標題句、切分與規則。資料庫那一半由 `verify` 對原文核對。"""

import argparse
import importlib.util
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("keyword_golden", TOOLS / "keyword_golden.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["keyword_golden"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


@pytest.mark.parametrize(
    ("line", "term"),
    [
        ("一、空氣污染物：指空氣中足以直接或間接妨害國民健康或生活環境之物質。", "空氣污染物"),
        ("（二）固定污染源：指移動污染源以外之污染源。", "固定污染源"),
        # 詞自己以數字開頭：編號只去掉前綴，不能連詞的第一個字一起去掉
        ("三、一般廢棄物：指事業廢棄物以外之廢棄物。", "一般廢棄物"),
        ("五、二氧化碳捕捉：指從排放源分離二氧化碳。", "二氧化碳捕捉"),
        # 括號內是別稱，不進詞
        ("七、減效裝置（Defeat devices）：指透過測量影響車輛運轉之裝置。", "減效裝置"),
        (
            "十一、道路系統小時均能音量（Leq,1h）：指一小時所測得之能量平均值。",
            "道路系統小時均能音量",
        ),
        ("四、使用者：謂使用本設施之人。", "使用者"),
    ],
)
def test_a_definition_line_yields_its_term(line: str, term: str) -> None:
    match = tool.DEFINITION_ITEM.match(line)

    assert match is not None
    assert match["term"] == term


@pytest.mark.parametrize(
    "line",
    [
        "一、主管機關：在中央為環境部。",  # 不是「指／謂／稱」的定義句
        "二、水：指以任何形式存在之地面水及地下水。",  # 兩個字，太泛
        "本法用詞，定義如下：",  # 標題句
        "三、第一項第一款：指申請許可者。".replace("：", "、"),  # 詞後面不是冒號
        "（一）電纜通道。",
    ],
)
def test_a_line_that_is_not_a_definition_is_not_matched(line: str) -> None:
    assert tool.DEFINITION_ITEM.match(line) is None


@pytest.mark.parametrize(
    "first_line",
    [
        "本法用詞，定義如下：",
        "本條例用辭之定義如左：",
        "本標準專用名詞及符號定義如下：",
        "本標準專有名詞定義及計算公式如下：",
        "本標準所稱各標準值、成分之意義如下：",
        "本節用詞定義規定如下：",
    ],
)
def test_a_defining_header_is_recognized(first_line: str) -> None:
    import re

    assert re.search(tool.DEFINING_HEADER, first_line)


@pytest.mark.parametrize(
    "first_line",
    [
        "本條例所稱飲用水，指供人飲用之水；其種類如下：",  # 分類清單
        "本辦法所定重要物資及固定設施，其類別及調查內涵如下：",
        "林產物分為下列二種：",
    ],
)
def test_a_classification_header_is_not_a_defining_header(first_line: str) -> None:
    import re

    assert re.search(tool.DEFINING_HEADER, first_line) is None


def test_the_split_depends_only_on_the_query() -> None:
    assert {tool.split_of(q) for q in ("固定污染源", "噪音管制標準", "露天燃燒")} <= {"dev", "test"}
    assert tool.split_of("固定污染源") == tool.split_of("固定污染源")
    assert {tool.split_of(f"詞{n}") for n in range(40)} == {"dev", "test"}


def test_the_item_number_prefix_is_removed_without_touching_the_term() -> None:
    assert tool.ITEM_NUMBER.sub("", "三、一般廢棄物：指…").startswith("一般廢棄物")
    assert tool.ITEM_NUMBER.sub("", "（十）十字路口：指…").startswith("十字路口")


class _Hit:
    """`ArticleHit` 的替身：只有規則會讀的欄位。"""

    def __init__(
        self, lexical: float, semantic: float | None, coverage: float, guidance: int = 0
    ) -> None:
        self.lexical_score = lexical
        self.semantic_similarity = semantic
        self.term_coverage = coverage
        self.guidance_documents = guidance

    def HasField(self, name: str) -> bool:  # protobuf 的方法名
        assert name == "semantic_similarity"
        return self.semantic_similarity is not None


def _rules() -> dict[str, Callable[[Any], bool]]:
    args = argparse.Namespace(lexical=0.5, semantic=0.71, coverage=0.5)
    return tool.rule_variants(args)  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("hit", "current", "whole_term", "semantic_needs_a_term"),
    [
        # 字面與語意都不到下界，條文含全部查詢詞：只有「整詞命中放行」列出（平台的新條件）
        (_Hit(0.3, 0.69, 1.0), False, True, False),
        # 整詞命中但字面臂沒命中（只由語意臂帶出）：不放行
        (_Hit(0.0, 0.5, 1.0), False, False, False),
        # 只含部分查詢詞、分數也不到：都不列
        (_Hit(0.3, 0.5, 0.5), False, False, False),
        # 語意達標但完全不含查詢詞：現行與 A 列出，B 不列
        (_Hit(0.0, 0.8, 0.0), True, True, False),
        # 函釋帶出：不看分數，三者都列
        (_Hit(0.0, None, 0.0, guidance=2), True, True, True),
    ],
)
def test_the_candidate_rules_differ_exactly_where_intended(
    hit: _Hit, current: bool, whole_term: bool, semantic_needs_a_term: bool
) -> None:
    rules = _rules()

    assert rules["列表規則"](hit) is current
    assert rules["候選 A：整詞命中放行"](hit) is whole_term
    assert rules["候選 B：語意命中要沾到字面"](hit) is semantic_needs_a_term


def test_whole_term_never_drops_a_hit_the_current_rule_lists() -> None:
    rules = _rules()
    hits = [
        _Hit(lexical, semantic, coverage, guidance)
        for lexical in (0.0, 0.3, 0.6)
        for semantic in (None, 0.5, 0.8)
        for coverage in (0.0, 0.5, 1.0)
        for guidance in (0, 1)
    ]

    for hit in hits:
        if rules["列表規則"](hit):
            assert rules["候選 A：整詞命中放行"](hit)


@pytest.mark.parametrize(
    ("record", "old_name"),
    [
        (
            "（原名稱：空氣污染防制法施行細則；新名稱：空氣污染防制法施行細則）",
            "空氣污染防制法施行細則",
        ),
        ("民國九十年修正（原名稱:廢棄物清理法;新名稱:廢棄物清理法）", "廢棄物清理法"),
    ],
)
def test_the_rename_pattern_extracts_the_old_name(record: str, old_name: str) -> None:
    found = re.search(tool.RENAME_PATTERN, record)
    assert found and found.group(1).strip() == old_name


def test_a_record_without_a_new_name_is_not_a_rename() -> None:
    assert re.search(tool.RENAME_PATTERN, "原名稱：某某辦法，其餘從略") is None


def test_the_hash_order_does_not_depend_on_the_input_order() -> None:
    terms = ["固定污染源", "移動污染源", "排放標準", "空氣污染物", "噪音"]
    assert tool.hash_order("s", terms) == tool.hash_order("s", list(reversed(terms)))
    # 鹽不同，順序不同：新題與原生題庫（鹽 pick）不會因為同一個排序而重疊
    assert tool.hash_order("fresh", terms) != tool.hash_order("pick", terms)


def test_a_fresh_row_is_a_term_class_query_in_its_own_split() -> None:
    row = tool.fresh_row("fresh_partial", "固定污染", ["O0020001-art-2"], "固定污染源")
    assert (row["cls"], row["split"], row["level"]) == ("term", "fresh", "article")
    assert row["term"] == "固定污染源" and row["answer"] == ["O0020001-art-2"]
    assert row["id"] == tool.fresh_row("fresh_partial", "固定污染", [])["id"]
    assert "term" not in tool.fresh_row("fresh_absent", "某某", [])


def test_the_answer_of_a_renamed_item_is_its_law_and_of_a_definition_its_article() -> None:
    renamed = {"bucket": "renamed", "expected": [{"pcode": "O0020001", "law": "某法"}]}
    definition = {"bucket": "definition", "expected": [{"article": "O0020001-art-3"}]}
    assert tool.answer_ids(renamed) == ["O0020001"]
    assert tool.answer_ids(definition) == ["O0020001-art-3"]
