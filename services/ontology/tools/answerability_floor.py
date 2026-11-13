"""Re-derive consult's relevance floors from the golden-set reports of the dev subset, offline.

    uv run python tools/answerability_floor.py \\
        --lexical eval/golden-retrieval-<date>.json --semantic eval/golden-retrieval-<date>.json

Each report records, per question, the best score of each arm over the top 50. A question
passes consult's floor when either arm reaches its floor, so the pass rate of the out-of-scope
(negative) questions is the share consult would answer instead of declining, and the pass rate
of the answerable ones is the share it would not wrongly decline.

The lexical scores come from the lexical arm of one report and the semantic ones from the hybrid
arm of another (the same file when one run measured both), joined by question id. Both must be
reports of the dev subset of the same split (`golden_retrieval_eval.py measure --subset dev`): the
floors are chosen on dev and the test subset is not read here; how a chosen floor does on unseen
questions is the end-to-end measurement on test. Only the reports are read: no database, no
embedding endpoint.

The floors this prints are not applied anywhere. Which point on the curve to take is a price
the owner of consult sets (how many answerable questions may be declined to catch how many
out-of-scope ones); the tool reports the curve and the point that maximises the difference
between the two pass rates (Youden's J).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Row:
    negative: bool
    lexical: float
    semantic: float


@dataclass(frozen=True)
class Point:
    lexical: float
    semantic: float
    negatives_pass: int
    answerable_pass: int
    negatives: int
    answerable: int

    @property
    def j(self) -> float:
        return self.answerable_pass / self.answerable - self.negatives_pass / self.negatives


def _results(report: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in report["arms"][arm]["results"]}


def require_dev(*reports: dict[str, Any]) -> None:
    """下界只在開發集上選：每份報告都是同一個切分的 dev。"""
    splits = {(r["meta"]["split"]["sha256"], r["meta"]["split"]["subset"]) for r in reports}
    if len(splits) != 1 or next(iter(splits))[1] != "dev":
        raise SystemExit(f"下界只在同一切分的開發集上選；得到 {sorted(splits)}")


def join(lexical: dict[str, Any], semantic: dict[str, Any]) -> list[Row]:
    """Negatives and scored answerable questions: lexical from one run, semantic from the other."""
    require_dev(lexical, semantic)
    lex = _results(lexical, "lexical")
    rows: list[Row] = []
    for qid, s in _results(semantic, "hybrid").items():
        if s["bucket"] != "negative" and s["status"] != "scored":
            continue  # unmapped: no article in the corpus to be found
        if qid not in lex:
            raise ValueError(f"question {qid} is missing from the lexical run")
        rows.append(
            Row(s["bucket"] == "negative", lex[qid]["top_lexical"] or 0.0, s["top_semantic"] or 0.0)
        )
    return rows


def evaluate(rows: Sequence[Row], lexical: float, semantic: float) -> Point:
    negatives = [r for r in rows if r.negative]
    answerable = [r for r in rows if not r.negative]

    def passes(r: Row) -> bool:
        return r.lexical >= lexical or r.semantic >= semantic

    return Point(
        lexical,
        semantic,
        sum(map(passes, negatives)),
        sum(map(passes, answerable)),
        len(negatives),
        len(answerable),
    )


def best(rows: Sequence[Row], lexicals: Sequence[float], semantics: Sequence[float]) -> Point:
    return max((evaluate(rows, lf, sf) for lf in lexicals for sf in semantics), key=lambda p: p.j)


#: 下界只寫在 ServiceDefaults 的共用檔一處（consult 與平台的搜尋端都讀它）；「現行」一列讀它。
RELEVANCE_FLOORS = (
    Path(__file__).resolve().parents[3]
    / "libs"
    / "dotnet"
    / "EnvCite.ServiceDefaults"
    / "relevance-floors.json"
)


def current_floors() -> tuple[float, float]:
    """現行的（字面、語意）下界。"""
    floors = json.loads(RELEVANCE_FLOORS.read_text(encoding="utf-8"))["Relevance"]
    return float(floors["LexicalFloor"]), float(floors["SemanticFloor"])


LEXICALS = (0.3, 0.5, 1.0, 2.0, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 8.0, float("inf"))
SEMANTICS = (*(round(0.6 + i * 0.01, 2) for i in range(25)), float("inf"))


def _row(label: str, p: Point) -> str:
    return (
        f"| {label} | {p.lexical:g} | {p.semantic:g}"
        f" | {p.negatives_pass} ({p.negatives_pass / p.negatives:.0%})"
        f" | {p.answerable_pass} ({p.answerable_pass / p.answerable:.0%}) | {p.j:.3f} |"
    )


def report(rows: Sequence[Row], current: tuple[float, float]) -> str:
    chosen = best(rows, LEXICALS, SEMANTICS)
    out = [
        f"questions: {sum(r.negative for r in rows)} negative,"
        f" {sum(not r.negative for r in rows)} answerable",
        "",
        "| point | lexical floor | semantic floor | negatives pass | answerable pass | J |",
        "|---|---|---|---|---|---|",
        _row("current", evaluate(rows, *current)),
        _row("max J", chosen),
        "",
        "Either arm alone (the other disabled):",
        "",
        "| lexical floor | negatives pass | answerable pass | J |",
        "|---|---|---|---|",
    ]
    for lf in LEXICALS[:-1]:
        p = evaluate(rows, lf, float("inf"))
        out.append(f"| {lf:g} | {p.negatives_pass} | {p.answerable_pass} | {p.j:.3f} |")
    out += ["", "| semantic floor | negatives pass | answerable pass | J |", "|---|---|---|---|"]
    for sf in SEMANTICS[:-1]:
        p = evaluate(rows, float("inf"), sf)
        out.append(f"| {sf:g} | {p.negatives_pass} | {p.answerable_pass} | {p.j:.3f} |")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lexical", type=Path, required=True, help="whole-corpus lexical report")
    parser.add_argument("--semantic", type=Path, required=True, help="scoped hybrid report")
    args = parser.parse_args()
    rows = join(
        json.loads(args.lexical.read_text(encoding="utf-8")),
        json.loads(args.semantic.read_text(encoding="utf-8")),
    )
    print(report(rows, current_floors()))


if __name__ == "__main__":
    main()
