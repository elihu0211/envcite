"""風險—覆蓋掃描：可答覆蓋率目標下，讓最少的無答案問句通過的兩個下界，並在留出的一半驗證。

    uv run python tools/floor_risk_coverage.py \
        --lexical eval/golden-retrieval-fullcorpus-2026-11-03.json \
        --semantic eval/golden-retrieval-fullcorpus-2026-11-03.json

輸入與配對同 `answerability_floor.py`（共用它的 `join`、`evaluate`、候選下界）。每個目標先在
隨機一半的題目上選下界，再在另一半驗證，20 次切半取平均。只印出結果，不改任何下界。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from answerability_floor import LEXICALS, SEMANTICS, Point, Row, evaluate, join

TARGETS = (0.99, 0.97, 0.95, 0.90, 0.85, 0.80)


def pick(rows: Sequence[Row], target: float) -> Point:
    """可答通過率達標的組合中，無答案放過最少者。"""
    feasible = [
        p
        for lf in LEXICALS
        for sf in SEMANTICS
        if (p := evaluate(rows, lf, sf)).answerable_pass / p.answerable >= target
    ]
    return min(feasible, key=lambda p: p.negatives_pass)


def held_out(rows: Sequence[Row], target: float, rounds: int = 20) -> tuple[float, float]:
    rng = random.Random(0)
    neg = ans = 0.0
    for _ in range(rounds):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        half = len(shuffled) // 2
        chosen = pick(shuffled[:half], target)
        e = evaluate(shuffled[half:], chosen.lexical, chosen.semantic)
        neg += e.negatives_pass / e.negatives
        ans += e.answerable_pass / e.answerable
    return neg / rounds, ans / rounds


def main() -> None:
    parser = argparse.ArgumentParser(prog="floor_risk_coverage")
    parser.add_argument("--lexical", type=Path, required=True)
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--current-lexical", type=float, default=4.0)
    parser.add_argument("--current-semantic", type=float, default=0.61)
    args = parser.parse_args()
    rows = join(json.loads(args.lexical.read_text()), json.loads(args.semantic.read_text()))
    now = evaluate(rows, args.current_lexical, args.current_semantic)
    print(
        f"現行（字面 {now.lexical:g}、語意 {now.semantic:g}）："
        f"無答案放過 {now.negatives_pass}／{now.negatives}，"
        f"可答通過 {now.answerable_pass}／{now.answerable}\n"
    )
    print("| 可答覆蓋率目標 | 全量題目上選出的下界 | 全量放過 | 留出放過 | 留出可答通過 |")
    print("|---|---|---|---|---|")
    for target in TARGETS:
        p = pick(rows, target)
        neg, ans = held_out(rows, target)
        print(
            f"| {target:.0%} | 字面 {p.lexical:g}、語意 {p.semantic:g}"
            f" | {p.negatives_pass}／{p.negatives} | {neg:.0%} | {ans:.0%} |"
        )


if __name__ == "__main__":
    main()
