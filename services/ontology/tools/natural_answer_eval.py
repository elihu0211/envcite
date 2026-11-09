"""把端到端接地檢查的逐題結果彙總成作答分類報告。

輸入是 `tools/grounding_check_eval.py` 寫出的 `.json`（`meta` 與 `results`）。本工具不呼叫服務、
不呼叫模型、沒有計時，報告裡的每個數字都是輸入檔逐題分類的計數；報告檔頭記輸入 run 的時間、
commit、題庫與語料快照（不記本工具的執行時間，所以同一輸入產出逐位元相同的報告）。延遲不在這裡：
見 `tools/load_bench.py`。

    uv run python tools/natural_answer_eval.py \\
        --input eval/grounding-check-<日期>.json \\
        [--split eval/golden-split.json --subset test] \\
        --out eval/natural-answer-quality-<日期>

沒有 `--out` 時只印出 Markdown。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs

from envcite_ontology.answer_quality import (
    InputError,
    evaluate_records,
    format_markdown_report,
)


def load_run(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """讀輸入 run；回傳 (meta，附輸入檔名與 sha256；results)。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    results = doc.get("results")
    meta = doc.get("meta")
    if not isinstance(results, list) or not isinstance(meta, dict):
        raise InputError(f"{path}: 不是 grounding_check_eval 的輸出（要有 meta 與 results）")
    return {**meta, "input_name": path.name, "input_sha256": gs.file_sha256(path)}, results


def select_subset(
    meta: dict[str, Any], results: list[dict[str, Any]], split_path: Path, subset: str
) -> list[dict[str, Any]]:
    """以切分檔篩出子集。切分檔必須與輸入 run 是同一份題庫；run 已是別的子集就拒絕。"""
    split_doc = gs.read(split_path)
    golden_sha = (meta.get("golden") or {}).get("sha256")
    if not golden_sha:
        raise InputError("輸入 run 的 meta 沒有題庫 sha256，無法確認切分檔與它同源")
    gs.require_golden(split_doc, golden_sha, str(split_path))
    ran = (meta.get("split") or {}).get("subset")
    if ran and ran != subset:
        raise InputError(f"輸入 run 只跑了 {ran} 子集，不能再篩出 {subset}")
    ids = gs.subset_ids(split_doc, subset)
    return [r for r in results if r.get("id") in ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--input", "-i", type=Path, required=True, help="grounding_check_eval 的 .json"
    )
    parser.add_argument("--split", "-s", type=Path, help="切分檔（與 --subset 一起給）")
    parser.add_argument("--subset", choices=["dev", "test"], help="只彙總這個子集")
    parser.add_argument("--out", "-o", type=Path, help="輸出路徑前綴（寫 .json 與 .md）")
    args = parser.parse_args()
    if bool(args.split) != bool(args.subset):
        parser.error("--split 與 --subset 要一起給")

    meta, results = load_run(args.input)
    if args.split:
        results = select_subset(meta, results, args.split, args.subset)
    if not results:
        raise SystemExit("沒有任何題目可彙總")
    summary = evaluate_records(results)
    md = format_markdown_report(summary, meta, args.subset)

    if not args.out:
        print(md)
        return
    out_json = args.out.parent / (args.out.name + ".json")
    out_md = args.out.parent / (args.out.name + ".md")
    out_json.write_text(
        json.dumps(
            {"input_run": meta, "subset": args.subset, "summary": summary.to_dict()},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    out_md.write_text(md, encoding="utf-8")
    print(f"已寫入：\n- {out_json}\n- {out_md}")


if __name__ == "__main__":
    try:
        main()
    except InputError as e:
        raise SystemExit(str(e)) from e
