"""字面檢索的延遲分佈與「優化前後排序、分數完全一致」的檢查（題庫外部，唯讀）。

    # 量測：每題先暖一輪，再量一輪；前 50 名與分數一併存下
    uv run python tools/lexical_latency.py run <golden.yaml> --out <run.json> [--max-df 0.005]

    # 比對兩次的結果：名次、IRI、字面分數逐一相同才算一致
    uv run python tools/lexical_latency.py compare <before.json> <after.json>

只走字面臂（不設嵌入）：`OntologyStore.search` 與服務同一條路徑，`limit` 取服務上限 50。
資料庫由 ENVCITE_ONTOLOGY_DSN 給。輸出不含題目文字，只有題號與結果。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from envcite_ontology.service import MAX_SEARCH_LIMIT
from envcite_ontology.settings import DatabaseSettings, ServerSettings, load
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def run(args: argparse.Namespace) -> None:
    items = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))["items"]
    questions = [(str(i["id"]), str(i["question"])) for i in items if i.get("question")]
    max_df = (
        args.max_df
        if args.max_df is not None
        else ServerSettings.model_fields["lexical_max_df"].default
    )
    db = load(DatabaseSettings)
    with OntologyStore.from_dsn(db.dsn) as store:
        segmenter = Segmenter(store.lexicon())

        def search(terms: str) -> list[dict[str, object]]:
            hits = store.search(
                terms,
                tokenizer=segmenter.tokenizer_id,
                limit=MAX_SEARCH_LIMIT,
                max_df=max_df,
            )
            return [{"iri": h.iri, "lexical_score": h.lexical_score} for h in hits]

        prepared = [(qid, segmenter.to_tsvector_input(q)) for qid, q in questions]
        if not args.no_warmup:  # 暖快取：量的是穩態延遲，不是冷啟動
            for _, terms in prepared:
                if terms:
                    search(terms)
        results = []
        for qid, terms in prepared:
            if not terms:
                continue
            t0 = time.perf_counter()
            hits = search(terms)
            results.append(
                {
                    "id": qid,
                    "terms": len(terms.split()),
                    "seconds": time.perf_counter() - t0,
                    "hits": hits,
                }
            )
    secs = [float(r["seconds"]) for r in results]  # type: ignore[arg-type]
    summary = {
        "n": len(secs),
        "p50": statistics.median(secs),
        "p95": percentile(secs, 0.95),
        "p99": percentile(secs, 0.99),
        "max": max(secs),
        "over_5s": sum(s > 5 for s in secs),
    }
    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": args.label,
        "max_df": max_df,
        "tokenizer": segmenter.tokenizer_id,
    }
    Path(args.out).write_text(
        json.dumps({"meta": meta, "summary": summary, "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({**meta, **summary}, ensure_ascii=False, indent=2))


def compare(args: argparse.Namespace) -> None:
    a = {r["id"]: r for r in json.loads(Path(args.before).read_text())["results"]}
    b = {r["id"]: r for r in json.loads(Path(args.after).read_text())["results"]}
    if a.keys() != b.keys():
        raise SystemExit(f"題目集合不同：{len(a.keys() ^ b.keys())} 題只在一邊")
    differ = [qid for qid in a if a[qid]["hits"] != b[qid]["hits"]]
    total_hits = sum(len(r["hits"]) for r in a.values())
    print(f"題數 {len(a)}，比對 {total_hits} 筆（IRI＋名次＋字面分數逐位元相同）")
    if differ:
        print(f"不一致 {len(differ)} 題：{differ[:10]}")
        raise SystemExit(1)
    print("完全一致")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("golden")
    r.add_argument("--out", required=True)
    r.add_argument("--label", default="")
    r.add_argument("--max-df", type=float, default=None, help="預設取伺服器設定的預設值")
    r.add_argument("--no-warmup", action="store_true", help="不暖快取（量冷查詢）")
    r.set_defaults(fn=run)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.set_defaults(fn=compare)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
