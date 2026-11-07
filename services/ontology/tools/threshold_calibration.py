"""Threshold calibration and gap isolation for answerability lower bounds.

Isolates:
1. Top-50 vs. Top-10 truncation effect (consult limit).
2. Lexical and semantic score distributions on dev vs. test.
3. False positive (unanswerable leaking) vs. false negative (answerable rejected) trade-offs.
4. Bucket-stratified pass rates (exact_id, paraphrase, multi_hop, negative).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs


@dataclass(frozen=True)
class ScoredQuestion:
    qid: str
    bucket: str
    is_answerable: bool
    top10_lexical: float
    top10_semantic: float | None
    top50_lexical: float
    top50_semantic: float | None


def analyze_truncation_gap(
    questions: Sequence[ScoredQuestion],
    *,
    lexical_bound: float = 0.5,
    semantic_bound: float = 0.71,
) -> dict[str, Any]:
    """隔離 Top-50 vs Top-10 截斷所造成的通過率落差。"""
    n_total = len(questions)
    if n_total == 0:
        return {}

    ans_questions = [q for q in questions if q.is_answerable]
    neg_questions = [q for q in questions if not q.is_answerable]

    def passes(lex: float, sem: float | None) -> bool:
        return lex >= lexical_bound or (sem is not None and sem >= semantic_bound)

    pass_top50_ans = sum(1 for q in ans_questions if passes(q.top50_lexical, q.top50_semantic))
    pass_top10_ans = sum(1 for q in ans_questions if passes(q.top10_lexical, q.top10_semantic))

    pass_top50_neg = sum(1 for q in neg_questions if passes(q.top50_lexical, q.top50_semantic))
    pass_top10_neg = sum(1 for q in neg_questions if passes(q.top10_lexical, q.top10_semantic))

    gap_ans = pass_top50_ans - pass_top10_ans
    gap_neg = pass_top50_neg - pass_top10_neg

    by_bucket: dict[str, dict[str, Any]] = {}
    buckets = sorted({q.bucket for q in questions})
    for b in buckets:
        bq = [q for q in questions if q.bucket == b]
        b_pass50 = sum(1 for q in bq if passes(q.top50_lexical, q.top50_semantic))
        b_pass10 = sum(1 for q in bq if passes(q.top10_lexical, q.top10_semantic))
        by_bucket[b] = {
            "count": len(bq),
            "pass_top50": b_pass50,
            "pass_top10": b_pass10,
            "rate_top50": round(b_pass50 / len(bq), 4) if bq else 0.0,
            "rate_top10": round(b_pass10 / len(bq), 4) if bq else 0.0,
            "truncation_drop": b_pass50 - b_pass10,
        }

    return {
        "answerable_count": len(ans_questions),
        "unanswerable_count": len(neg_questions),
        "answerable_pass_top50": pass_top50_ans,
        "answerable_pass_top10": pass_top10_ans,
        "answerable_pass_rate_top50": round(pass_top50_ans / len(ans_questions), 4)
        if ans_questions
        else 0.0,
        "answerable_pass_rate_top10": round(pass_top10_ans / len(ans_questions), 4)
        if ans_questions
        else 0.0,
        "truncation_gap_answerable": gap_ans,
        "truncation_gap_unanswerable": gap_neg,
        "by_bucket": by_bucket,
    }


def calibrate_thresholds(
    questions: Sequence[ScoredQuestion],
    *,
    lexical_grid: Sequence[float] = (0.3, 0.5, 0.8, 1.0, 1.5, 2.0),
    semantic_grid: Sequence[float] = (0.60, 0.65, 0.70, 0.71, 0.75, 0.80),
    limit_depth: int = 10,
) -> list[dict[str, Any]]:
    """以給定候選深度計算不同門檻組合的風險—覆蓋表現，供下界重訂。"""
    ans = [q for q in questions if q.is_answerable]
    neg = [q for q in questions if not q.is_answerable]

    results = []
    for l_th in lexical_grid:
        for s_th in semantic_grid:

            def matches(q: ScoredQuestion, l_target: float = l_th, s_target: float = s_th) -> bool:
                lex = q.top10_lexical if limit_depth == 10 else q.top50_lexical
                sem = q.top10_semantic if limit_depth == 10 else q.top50_semantic
                return lex >= l_target or (sem is not None and sem >= s_target)

            t_ans = sum(1 for q in ans if matches(q))
            t_neg = sum(1 for q in neg if matches(q))

            cov = t_ans / len(ans) if ans else 0.0
            leak = t_neg / len(neg) if neg else 0.0

            results.append(
                {
                    "lexical_threshold": l_th,
                    "semantic_threshold": s_th,
                    "coverage_answerable": round(cov, 4),
                    "leak_unanswerable": round(leak, 4),
                    "retained_answerable": t_ans,
                    "leaked_unanswerable": t_neg,
                }
            )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="可答性下界落差隔離與重訂分析")
    parser.add_argument(
        "--eval-json", type=Path, required=True, help="golden-retrieval 評估結果 JSON"
    )
    parser.add_argument("--out", type=Path, help="輸出分析 JSON 報表路徑")
    args = parser.parse_args()

    data = json.loads(args.eval_json.read_text(encoding="utf-8"))
    rows = data.get("rows", data.get("results", []))

    parsed: list[ScoredQuestion] = []
    for r in rows:
        qid = str(r["id"])
        bucket = str(r.get("bucket", "paraphrase"))
        is_ans = bucket != gs.NEGATIVE and r.get("status") != "negative"
        top10_l = float(r.get("top_lexical", 0.0) or 0.0)
        top10_s = float(r["top_semantic"]) if r.get("top_semantic") is not None else None
        top50_l = float(r.get("top50_lexical", top10_l) or top10_l)
        top50_s = float(r["top50_semantic"]) if r.get("top50_semantic") is not None else top10_s

        parsed.append(
            ScoredQuestion(
                qid=qid,
                bucket=bucket,
                is_answerable=is_ans,
                top10_lexical=top10_l,
                top10_semantic=top10_s,
                top50_lexical=top50_l,
                top50_semantic=top50_s,
            )
        )

    isolation = analyze_truncation_gap(parsed)
    grid = calibrate_thresholds(parsed, limit_depth=10)

    report = {
        "total_questions": len(parsed),
        "isolation_analysis": isolation,
        "calibration_grid_top10": grid,
    }

    out_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text + "\n", encoding="utf-8")
        print(f"落差隔離報告已寫入 {args.out}")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
