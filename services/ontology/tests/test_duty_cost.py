"""重抽成本與浪費的計數（tools/duty_cost.py）：日誌摘要與重複確認的判定。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "duty_cost.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("duty_cost_module", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["duty_cost_module"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

LOG = """2026-11-09 11:18:14,402 INFO 法規 2 部：O0040001、O0040010
2026-11-09 11:21:34,706 WARNING 丟掉 O0040001 art_8：quote is not in the text
2026-11-09 13:05:00,001 INFO 322 個單元，310 抽取，50 個確認、200 個駁回，3 輪
2026-11-09 13:05:00,002 INFO 抽取計數：呼叫 1234，修復 56，語法失敗 7，形狀問題 8；\
詞元 {'prompt': 900, 'completion': 100}
"""


def test_the_log_summary_has_calls_tokens_rounds_and_duration() -> None:
    got = tool.parse_log(LOG)
    assert got["extract_calls"] == 1234 and got["repairs"] == 56
    assert got["tokens"] == {"prompt": 900, "completion": 100}
    assert got["rounds"] == 3
    assert got["seconds"] == 6406


def test_a_log_without_the_summary_gives_none_not_zero() -> None:
    got = tool.parse_log("2026-11-09 11:18:14,402 INFO 開始\n")
    assert got["extract_calls"] is None and got["rounds"] is None and got["seconds"] is None


def test_spans_overlap_only_when_they_share_a_character() -> None:
    assert tool.overlaps((0, 10), (9, 20))
    assert not tool.overlaps((0, 10), (10, 20))


def _s(sid: int, unit: int, rnd: int, span: tuple[int, int], confirmed: bool) -> dict[str, Any]:
    return {
        "id": sid,
        "provision_id": unit,
        "round": rnd,
        "span": list(span),
        "confirmed": confirmed,
    }


def test_a_later_round_statement_over_an_earlier_confirmed_quote_is_redundant() -> None:
    rows = [
        _s(1, 1, 0, (0, 10), True),  # 第 0 輪已確認
        _s(2, 1, 1, (5, 15), False),  # 第 1 輪重抽，與它重疊：重複
        _s(3, 1, 1, (20, 30), True),  # 第 1 輪，另一段：不重複
        _s(4, 1, 2, (20, 30), False),  # 第 2 輪，與第 1 輪已確認的重疊：重複
        _s(5, 2, 1, (0, 10), False),  # 別的單元
    ]
    assert tool.redundant(rows) == [2, 4]


def test_a_statement_confirmed_in_the_same_round_is_not_waste_for_its_peers() -> None:
    rows = [_s(1, 1, 1, (0, 10), True), _s(2, 1, 1, (0, 10), True)]
    assert tool.redundant(rows) == []


def test_round_zero_is_never_redundant() -> None:
    rows = [_s(1, 1, 0, (0, 10), True), _s(2, 1, 0, (0, 10), True)]
    assert tool.redundant(rows) == []
