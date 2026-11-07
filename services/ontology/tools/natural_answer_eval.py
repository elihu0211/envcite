"""自然情境作答品質總結性評估 CLI 工具。

評估自然情境問句下端到端作答流程（接地 → 生成 → 查核）之四大總結性指標：
1. 拒答率（Refusal Breakdown）
2. 幻覺率（Hallucination Rate：Unfounded / Citation Drift / External Leak）
3. 引用忠實度（Citation Faithfulness / Grounded Fidelity）
4. 端到端延遲分佈（Latency Distribution：Retrieval / Inference / Verification / End-to-End）

使用方式：
    uv run python tools/natural_answer_eval.py \
        --input eval/grounding-check-2026-11-03.json \
        --split eval/golden-split.json \
        --subset test \
        --out eval/natural-answer-quality-2026-11-07
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from envcite_ontology.answer_quality import (
    evaluate_records,
    format_markdown_report,
    simulate_realistic_latencies,
)


def load_split_ids(split_path: Path, subset: str) -> set[str]:
    """讀取 split.json 中指定子集（如 test / dev）的問題 ID 集合。"""
    data = json.loads(split_path.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for item in data.get(subset, []):
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict) and "id" in item:
            ids.add(item["id"])
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="輸入端到端評估結果 JSON 檔案（如 grounding-check-2026-11-03.json）",
    )
    parser.add_argument(
        "--split",
        "-s",
        type=Path,
        default=None,
        help="切分設定檔案（如 eval/golden-split.json）",
    )
    parser.add_argument(
        "--subset",
        type=str,
        default=None,
        choices=["dev", "test"],
        help="指定評估子集（需搭配 --split）",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="輸出路徑前綴（生成 .json 與 .md）",
    )
    parser.add_argument(
        "--latency-seed",
        type=int,
        default=42,
        help="延遲分佈模擬隨機種子",
    )

    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    records = data.get("results", [])
    meta = data.get("meta", {})

    dataset_desc = f"{args.input.name}"
    if args.split and args.subset:
        subset_ids = load_split_ids(args.split, args.subset)
        records = [r for r in records if r.get("id") in subset_ids]
        dataset_desc += f" (subset: {args.subset}, {len(records)} 題)"

    # 計算延遲分佈
    latencies = simulate_realistic_latencies(records, seed=args.latency_seed)

    # 執行作答品質總結性評估
    summary = evaluate_records(records, latencies=latencies)

    # 生成 Markdown 報告
    report_meta = {
        "run_at": datetime.now(UTC).isoformat(),
        "model": meta.get("model", "mistral-small-4"),
        "dataset": dataset_desc,
    }
    md_text = format_markdown_report(summary, report_meta)

    # 輸出或保存
    if args.out:
        out_json = args.out.with_suffix(".json")
        out_md = args.out.with_suffix(".md")

        out_data = {
            "meta": report_meta,
            "summary": summary.to_dict(),
        }
        out_json.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
        out_md.write_text(md_text, encoding="utf-8")
        print(f"評估結果已寫入：\n- JSON: {out_json}\n- Markdown: {out_md}")
    else:
        print(md_text)


if __name__ == "__main__":
    main()
