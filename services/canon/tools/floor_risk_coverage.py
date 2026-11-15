"""風險—覆蓋掃描：可答覆蓋率目標下，讓最少的無答案問句通過的兩個下界（只看開發集）。

    uv run python tools/floor_risk_coverage.py \
        --lexical eval/golden-retrieval-<date>.json --semantic eval/golden-retrieval-<date>.json

輸入與配對同 `answerability_floor.py`（共用它的 `join`、`evaluate`、候選下界），只接受同一切分
開發集的報告；選定的下界在測試集上的表現由端到端量測報告。只印出結果，不改任何下界。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answerability_floor import LEXICALS, SEMANTICS, Point, Row, current_floors, evaluate, join

TARGETS = (0.99, 0.97, 0.95, 0.90, 0.85, 0.80)


def pick(rows: Sequence[Row], target: float) -> Point | None:
    """可答通過率達標的組合中，無答案放過最少者；格點內沒有組合達標時為 None。"""
    feasible = [
        p
        for lf in LEXICALS
        for sf in SEMANTICS
        if (p := evaluate(rows, lf, sf)).answerable_pass / p.answerable >= target
    ]
    return min(feasible, key=lambda p: p.negatives_pass, default=None)


def main() -> None:
    parser = argparse.ArgumentParser(prog="floor_risk_coverage")
    parser.add_argument("--lexical", type=Path, required=True)
    parser.add_argument("--semantic", type=Path, required=True)
    args = parser.parse_args()
    rows = join(json.loads(args.lexical.read_text()), json.loads(args.semantic.read_text()))
    now = evaluate(rows, *current_floors())
    print(
        f"現行（字面 {now.lexical:g}、語意 {now.semantic:g}）："
        f"無答案放過 {now.negatives_pass}／{now.negatives}，"
        f"可答通過 {now.answerable_pass}／{now.answerable}\n"
    )
    print("| 可答覆蓋率目標 | 開發集上選出的下界 | 無答案放過 | 可答通過 |")
    print("|---|---|---|---|")
    for target in TARGETS:
        p = pick(rows, target)
        if p is None:
            print(f"| {target:.0%} | 格點內達不到 | — | — |")
            continue
        print(
            f"| {target:.0%} | 字面 {p.lexical:g}、語意 {p.semantic:g}"
            f" | {p.negatives_pass}／{p.negatives} | {p.answerable_pass}／{p.answerable} |"
        )


if __name__ == "__main__":
    main()
