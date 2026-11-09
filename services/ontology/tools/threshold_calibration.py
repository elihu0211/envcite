"""How much of the answerability floor's pass rate depends on seeing 10 hits instead of k.

    uv run python tools/threshold_calibration.py --eval eval/golden-retrieval-dev-<date>.json \\
        [--arm hybrid] [--consult-settings ../consult/src/EnvCite.Consult/appsettings.json] \\
        --out eval/golden-truncation-dev-<date>.json

Input is the report `golden_retrieval_eval.py measure` writes: per question the highest lexical and
semantic score among the first k hits (`top_lexical`, `top_semantic`) and among the first
`CONSULT_LIMIT` hits (`top10_lexical`, `top10_semantic`), the ones consult actually reads. A report
without those fields, or one whose floors cannot be read, stops the run: nothing is defaulted, since
a missing field read as "same as the other depth" shows a gap of zero for every input.

The floors are consult's (`Relevance` in its appsettings.json), the one place they are set. A
question passes when any hit reaches the lexical floor or the semantic floor, which is exactly
`Relevance.IsRelevant` over a list of articles.

Isolated: the questions that pass on the first k hits and fail on the first 10 (`truncation gap`;
the first 10 are a prefix of the first k, so a question cannot pass on 10 and fail on k), counted
separately for answerable and unanswerable questions and per bucket. The floor grid then shows,
for the depth consult reads, the answerable pass rate against the share of unanswerable questions
that pass.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs

CONSULT_SETTINGS = (
    Path(__file__).resolve().parents[2] / "consult" / "src" / "EnvCite.Consult" / "appsettings.json"
)

LEXICAL_GRID = (0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 4.0)
SEMANTIC_GRID = (0.60, 0.65, 0.70, 0.71, 0.75, 0.80)


@dataclass(frozen=True)
class ScoredQuestion:
    qid: str
    bucket: str
    is_answerable: bool
    #: 前 k 條的最高分（k 見報告的 `meta.retrieval.k`）。
    deep_lexical: float
    deep_semantic: float | None
    #: 前 10 條（consult 讀的）的最高分。
    top10_lexical: float
    top10_semantic: float | None


@dataclass(frozen=True)
class Floors:
    lexical: float
    semantic: float


def read_floors(path: Path) -> Floors:
    """consult 的 `Relevance` 區段；缺任一鍵就停，不補預設。"""
    section = json.loads(path.read_text(encoding="utf-8")).get("Relevance")
    if not isinstance(section, Mapping):
        raise ValueError(f"{path} 沒有 Relevance 區段")
    missing = [k for k in ("LexicalFloor", "SemanticFloor") if k not in section]
    if missing:
        raise ValueError(f"{path} 的 Relevance 缺 {', '.join(missing)}")
    return Floors(float(section["LexicalFloor"]), float(section["SemanticFloor"]))


def passes(lexical: float, semantic: float | None, floors: Floors) -> bool:
    return lexical >= floors.lexical or (semantic is not None and semantic >= floors.semantic)


def questions_of(report: Mapping[str, Any], arm: str) -> tuple[list[ScoredQuestion], int]:
    """報告某一臂的計分題與 negative 題；unmapped 沒有跑檢索，不在內。回傳（題, unmapped 題數）。

    缺 `top10_*` 或前 k 條的欄位、前 10 條的分數高於前 k 條，都是輸入錯誤，整份拒絕。
    """
    try:
        rows = report["arms"][arm]["results"]
    except KeyError as e:
        raise ValueError(
            f"報告沒有 {e.args[0]!r}（臂 {arm!r}）；measure 的輸出才有 arms.<臂>.results"
        ) from e
    out: list[ScoredQuestion] = []
    unmapped = 0
    problems: list[str] = []
    for r in rows:
        status = r["status"]
        if status == "unmapped":
            unmapped += 1
            continue
        if status not in ("scored", "negative"):
            problems.append(f"{r['id']}：未知的 status {status!r}")
            continue
        absent = [k for k in ("top_lexical", "top10_lexical") if r.get(k) is None]
        if absent:
            problems.append(
                f"{r['id']}：缺 {', '.join(absent)}（報告不是這個版本的 measure 寫的？）"
            )
            continue
        deep_s, top_s = r.get("top_semantic"), r.get("top10_semantic")
        deep_l, top_l = float(r["top_lexical"]), float(r["top10_lexical"])
        if top_l > deep_l or (top_s is not None and (deep_s is None or top_s > deep_s)):
            problems.append(f"{r['id']}：前 10 條的最高分高於前 k 條，不可能")
            continue
        out.append(
            ScoredQuestion(
                qid=str(r["id"]),
                bucket=str(r["bucket"]),
                is_answerable=status == "scored",
                deep_lexical=deep_l,
                deep_semantic=None if deep_s is None else float(deep_s),
                top10_lexical=top_l,
                top10_semantic=None if top_s is None else float(top_s),
            )
        )
    if problems:
        raise ValueError("報告有問題，整份不分析：\n" + "\n".join(problems[:20]))
    return out, unmapped


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """二項比例的 Wilson 95% 區間；n = 0 時為 None。"""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)


def _group(qs: Sequence[ScoredQuestion], floors: Floors) -> dict[str, Any]:
    deep = [q.qid for q in qs if passes(q.deep_lexical, q.deep_semantic, floors)]
    top = {q.qid for q in qs if passes(q.top10_lexical, q.top10_semantic, floors)}
    gap = [q for q in deep if q not in top]
    n = len(qs)
    return {
        "n": n,
        "pass_deep": len(deep),
        "pass_top10": len(top),
        "rate_deep": round(len(deep) / n, 4) if n else None,
        "rate_top10": round(len(top) / n, 4) if n else None,
        "truncation_gap": len(gap),
        "truncation_gap_rate": round(len(gap) / n, 4) if n else None,
        "truncation_gap_rate_ci95": wilson(len(gap), n),
        "gap_questions": sorted(gap),
    }


def analyze_truncation_gap(questions: Sequence[ScoredQuestion], floors: Floors) -> dict[str, Any]:
    """前 k 條過下界、前 10 條不過的題：可答與無答案各一組，另依桶分。"""
    ans = [q for q in questions if q.is_answerable]
    neg = [q for q in questions if not q.is_answerable]
    return {
        "answerable": _group(ans, floors),
        "unanswerable": _group(neg, floors),
        "by_bucket": {
            b: _group([q for q in questions if q.bucket == b], floors)
            for b in sorted({q.bucket for q in questions})
        },
    }


def calibrate_thresholds(
    questions: Sequence[ScoredQuestion],
    *,
    depth: str,
    lexical_grid: Sequence[float] = LEXICAL_GRID,
    semantic_grid: Sequence[float] = SEMANTIC_GRID,
) -> list[dict[str, Any]]:
    """給定的候選深度（`top10` 或 `deep`）下各下界組合的覆蓋與放過。"""
    if depth not in ("top10", "deep"):
        raise ValueError(f"depth 只有 top10 與 deep：{depth}")
    ans = [q for q in questions if q.is_answerable]
    neg = [q for q in questions if not q.is_answerable]

    def view(q: ScoredQuestion) -> tuple[float, float | None]:
        return (
            (q.top10_lexical, q.top10_semantic)
            if depth == "top10"
            else (q.deep_lexical, q.deep_semantic)
        )

    results = []
    for lex in lexical_grid:
        for sem in semantic_grid:
            floors = Floors(lex, sem)
            kept = sum(passes(*view(q), floors) for q in ans)
            leaked = sum(passes(*view(q), floors) for q in neg)
            results.append(
                {
                    "lexical_floor": lex,
                    "semantic_floor": sem,
                    "answerable_pass": kept,
                    "unanswerable_pass": leaked,
                    "coverage_answerable": round(kept / len(ans), 4) if ans else None,
                    "leak_unanswerable": round(leaked / len(neg), 4) if neg else None,
                }
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="可答性下界：前 10 條與前 k 條的落差隔離")
    parser.add_argument("--eval", type=Path, required=True, help="measure 寫的報告 JSON")
    parser.add_argument("--arm", default="hybrid", help="報告裡的臂；下界用在混合檢索上")
    parser.add_argument("--consult-settings", type=Path, default=CONSULT_SETTINGS)
    parser.add_argument("--out", type=Path, help="輸出 JSON；省略只印出")
    args = parser.parse_args()

    report = json.loads(args.eval.read_text(encoding="utf-8"))
    floors = read_floors(args.consult_settings)
    qs, unmapped = questions_of(report, args.arm)
    meta = report["meta"]
    out = {
        "input": {
            "path": args.eval.name,
            "sha256": gs.file_sha256(args.eval),
            "arm": args.arm,
            "subset": meta["split"]["subset"],
            "k": meta["retrieval"]["k"],
            "envcite_commit": meta["envcite_commit"],
            "snapshots": meta["snapshots"],
            "unmapped_not_run": unmapped,
        },
        "floors": {
            "lexical": floors.lexical,
            "semantic": floors.semantic,
            "source": str(args.consult_settings.name) + " 的 Relevance 區段",
        },
        "total_questions": len(qs),
        "truncation": analyze_truncation_gap(qs, floors),
        "grid_top10": calibrate_thresholds(qs, depth="top10"),
        "grid_deep": calibrate_thresholds(qs, depth="deep"),
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"落差隔離報告已寫入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
