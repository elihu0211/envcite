"""陳述進入推論後的涵攝評估工具（Subsumption Evaluation Tool）。

以真實條文規範陳述為大前提、標準案件事實為小前提，呼叫符號推理機（clingo），
客觀量測三值 Kleene 涵攝邏輯、缺漏事實識別、不確定概念裁量判斷、責任主體分流與行政罰法總則阻卻。

用法：
    uv run python tools/subsumption_eval.py
    uv run python tools/subsumption_eval.py --dsn postgresql://...
    uv run python tools/subsumption_eval.py --out report.json --markdown report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from envcite_ontology.reasoning import (
    SubsumptionCase,
    evaluate_subsumption,
)

DEFAULT_BENCHMARK = Path(__file__).resolve().parents[1] / "eval" / "subsumption_benchmark.json"


def load_benchmark(path: Path) -> list[SubsumptionCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[SubsumptionCase] = []
    for item in data:
        as_of = date.fromisoformat(item["as_of"])
        decided_on = date.fromisoformat(item["decided_on"]) if item.get("decided_on") else None
        cases.append(
            SubsumptionCase(
                case_id=item["case_id"],
                title=item["title"],
                description=item.get("description", ""),
                as_of=as_of,
                decided_on=decided_on,
                case_facts=item["case_facts"],
                expected_verdicts=item["expected_verdicts"],
                expected_penalties=tuple(
                    (p[0], p[1], bool(p[2])) for p in item.get("expected_penalties", [])
                ),
                expected_missing=tuple((m[0], m[1]) for m in item.get("expected_missing", [])),
                expected_judgements=tuple(
                    (j[0], j[1]) for j in item.get("expected_judgements", [])
                ),
                expected_unpunished=tuple(item.get("expected_unpunished", [])),
                expected_time_barred=tuple(item.get("expected_time_barred", [])),
            )
        )
    return cases


def _load_id_resolver(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """從資料庫讀取 statement 與 sanction 錨定標籤對映。"""
    rows = conn.execute(
        "SELECT s.id, p.path || '/' || COALESCE(s.statement_kind::text, '') "
        "  FROM annotation.statement s "
        "  JOIN corpus.provision p ON p.id = s.provision_id"
    ).fetchall()
    mapping: dict[str, int] = {}
    for sid, ref in rows:
        mapping[ref] = int(sid)
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="陳述進入推論後的涵攝評估工具",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ENVCITE_ONTOLOGY_DSN"),
        help="PostgreSQL 連線字串（未指定則讀 ENVCITE_ONTOLOGY_DSN）",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=DEFAULT_BENCHMARK,
        help="涵攝黃金評估案例 JSON 路徑",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="輸出 JSON 報告路徑",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="輸出 Markdown 報告路徑",
    )

    args = parser.parse_args(argv)

    if not args.benchmark.exists():
        sys.exit(f"錯誤：找不到評估案例檔案 {args.benchmark}")

    cases = load_benchmark(args.benchmark)
    print(f"載入 {len(cases)} 個涵攝評估案例：{args.benchmark.name}")

    if not args.dsn:
        sys.exit("錯誤：未指定 --dsn 且未設定環境變數 ENVCITE_ONTOLOGY_DSN")

    with psycopg.connect(args.dsn) as conn:
        resolver = _load_id_resolver(conn)
        report = evaluate_subsumption(conn, cases, id_resolver=resolver)

    md_report = report.to_markdown()
    print("\n" + md_report)

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md_report, encoding="utf-8")
        print(f"已儲存 Markdown 報告至：{args.markdown}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已儲存 JSON 報告至：{args.out}")

    return 0 if report.passed_cases == report.total_cases else 1


if __name__ == "__main__":
    sys.exit(main())
