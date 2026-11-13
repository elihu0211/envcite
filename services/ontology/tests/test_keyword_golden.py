"""關鍵字題庫工具的純邏輯：定義行的抽取、標題句、切分與規則。資料庫那一半由 `verify` 對原文核對。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
