"""Ablation study on reciprocal rank fusion (RRF) constant k and candidate pool size P.

Usage:
    uv run python tools/ablation_fusion_eval.py --qrels eval/golden-qrels.json \\
        --runs-lex eval/golden-retrieval-dev-lexical.run.json \\
        --runs-sem eval/golden-retrieval-dev-semantic.run.json \\
        --split eval/golden-split.json \\
        --out eval/golden-ablation-fusion.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs

KS_DEFAULT = (20, 60, 100)
POOLS_DEFAULT = (30, 50, 100)
EVAL_METRICS = ("hit_rate@1", "hit_rate@5", "hit_rate@10", "hit_rate@50", "mrr", "ndcg@10")


@dataclass(frozen=True)
class AblationConfig:
    k: int
    pool: int


def rrf_fuse(
    lex_ranking: Sequence[str],
    sem_ranking: Sequence[str],
    *,
    k: int = 60,
    pool: int = 50,
) -> list[str]:
    """以名次倒數融合兩臂前 pool 名條文。

    同分時依 (法規, 條號) 決定確定性次序，避免隨機抖動。
    """
    scores: dict[str, float] = defaultdict(float)

    # 字面臂
    for rank, doc in enumerate(lex_ranking[:pool], 1):
        scores[doc] += 1.0 / (k + rank)

    # 語意臂
    for rank, doc in enumerate(sem_ranking[:pool], 1):
        scores[doc] += 1.0 / (k + rank)

    # 排序：分數降序，同分以字串字典序破平（與資料庫 tie-breaker 精神一致）
    sorted_docs = sorted(scores.keys(), key=lambda d: (-scores[d], d))
    return sorted_docs


def run_ablation(
    qrels: gs.Qrels,
    lex_runs: Mapping[str, Sequence[str]],
    sem_runs: Mapping[str, Sequence[str]],
    qids: Sequence[str],
    *,
    k_values: Sequence[int] = KS_DEFAULT,
    pool_values: Sequence[int] = POOLS_DEFAULT,
    metrics: Sequence[str] = EVAL_METRICS,
    relevant_from: int = gs.RELEVANT_FROM,
) -> dict[str, Any]:
    """對各 (k, pool) 組合計算指標，回傳評估矩陣。"""
    active_qrels = {qid: qrels[qid] for qid in qids if qid in qrels}
    results: list[dict[str, Any]] = []

    for pool in pool_values:
        for k in k_values:
            fused_ranking: dict[str, list[str]] = {}
            for qid in active_qrels:
                lex = lex_runs.get(qid, [])
                sem = sem_runs.get(qid, [])
                fused_ranking[qid] = rrf_fuse(lex, sem, k=k, pool=pool)

            scores = gs.evaluate(
                active_qrels,
                fused_ranking,
                list(metrics),
                relevant_from=relevant_from,
            )

            results.append(
                {
                    "k": k,
                    "pool": pool,
                    "metrics": scores,
                }
            )

    return {
        "num_questions": len(active_qrels),
        "k_values": list(k_values),
        "pool_values": list(pool_values),
        "grid": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RRF 融合常數與候選池深度消融評估")
    parser.add_argument("--qrels", type=Path, required=True, help="golden-qrels.json 路徑")
    parser.add_argument("--runs-lex", type=Path, required=True, help="字面臂 run 檔")
    parser.add_argument("--runs-sem", type=Path, required=True, help="語意臂 run 檔")
    parser.add_argument("--split", type=Path, required=True, help="golden-split.json 路徑")
    parser.add_argument("--subset", choices=["dev", "test"], default="dev", help="評估子集")
    parser.add_argument("--out", type=Path, help="輸出 JSON 報表路徑")
    args = parser.parse_args()

    qrels_doc = gs.read(args.qrels)
    split_doc = json.loads(args.split.read_text(encoding="utf-8"))
    lex_doc = json.loads(args.runs_lex.read_text(encoding="utf-8"))
    sem_doc = json.loads(args.runs_sem.read_text(encoding="utf-8"))

    qids = split_doc[args.subset]
    lex_runs = lex_doc.get("run", lex_doc)
    sem_runs = sem_doc.get("run", sem_doc)

    report = run_ablation(
        qrels_doc["qrels"],
        lex_runs,
        sem_runs,
        qids,
        relevant_from=qrels_doc.get("relevant_from", gs.RELEVANT_FROM),
    )

    out_content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_content + "\n", encoding="utf-8")
        print(f"消融報告已寫入 {args.out}")
    else:
        print(out_content)


if __name__ == "__main__":
    main()
