"""入口列表的相關性規則在不同的查詢詞覆蓋率門檻下，對黃金題庫的影響（唯讀）。

    uv run python tools/list_coverage_eval.py <golden.yaml> --qrels … --split … \\
        --subset dev --out eval/list-coverage-dev-<日期>

列表（platform 的搜尋頁與 `POST /v2/search`）一條命中要通過下界才列出：語意相似度達
`semantic_floor`，或字面分數達 `lexical_floor` 且 `SearchArticles` 回的 `term_coverage` 達門檻
（0 = 不看覆蓋率，即 consult 的規則）。這支工具以服務自己的取候選與覆蓋率
（`OntologyStore.search`、`searched_terms`、`term_coverage`），對子集的每題取前 `--k` 條，
算各門檻下：

- 可答題：列得出至少一條（有結果）、列出的有相關條（qrels 的相關分數以上，召回）。
- 無答案題：列得出至少一條（誤放，越低越好）。

門檻只能用 `dev` 選；`test` 在定案後報告一次。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_retrieval_eval as gre
import golden_set as gs

from envcite_ontology.coverage import term_coverage
from envcite_ontology.settings import ModelsSettings, ServerSettings, load
from envcite_ontology.store import OntologyStore
from envcite_ontology.verification import config
from envcite_segmenter import Segmenter

GRID = (0.0, 0.2, 0.34, 0.4, 0.5, 0.6, 0.7, 0.8)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("golden", type=Path)
    p.add_argument("--qrels", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--subset", choices=("dev", "test"), required=True)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--lexical-floor", type=float, default=0.5)
    p.add_argument("--semantic-floor", type=float, default=0.71)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    items, golden_sha = gre._golden(args.golden)
    qrels_doc, split_doc = gs.read(args.qrels), gs.read(args.split)
    gs.require_golden(qrels_doc, golden_sha, str(args.qrels))
    gs.require_golden(split_doc, golden_sha, str(args.split))
    qrels: gs.Qrels = qrels_doc["qrels"]
    relevant_from = int(qrels_doc["relevant_from"])
    rows = [
        r
        for r in gre.questions(items, qrels_doc, gs.subset_ids(split_doc, args.subset))
        if r.status in ("scored", "negative")
    ]
    settings = load(ServerSettings)
    embedder = config.embedder_from_settings(load(ModelsSettings), timeout=30.0)
    on = date.today().isoformat()
    counts: dict[float, dict[str, int]] = {t: {} for t in GRID}

    def bump(theta: float, key: str) -> None:
        counts[theta][key] = counts[theta].get(key, 0) + 1

    answerable = negative = 0
    with OntologyStore.from_dsn(settings.dsn) as store:
        segmenter = Segmenter(store.lexicon())
        law_names = store.law_names()
        for r in rows:
            terms = segmenter.to_tsvector_input(r.question)
            hits = store.search(
                terms,
                tokenizer=segmenter.tokenizer_id,
                as_of=on,
                limit=args.k,
                embedding=embedder.embed([r.question])[0] if embedder else None,
                model=embedder.model if embedder else "",
                max_df=settings.lexical_max_df,
                laws=law_names.find(r.question),
            )
            searched = store.searched_terms(
                terms, tokenizer=segmenter.tokenizer_id, max_df=settings.lexical_max_df
            )
            coverage = [term_coverage(searched, h.content, segmenter) for h in hits]
            relevant = {d for d, g in qrels.get(r.id, {}).items() if g >= relevant_from}
            if r.status == "negative":
                negative += 1
            else:
                answerable += 1
            for theta in GRID:
                passing = [
                    h
                    for h, c in zip(hits, coverage, strict=True)
                    if (h.semantic_similarity or 0.0) >= args.semantic_floor
                    or (h.lexical_score >= args.lexical_floor and c >= theta)
                ]
                if r.status == "negative":
                    if passing:
                        bump(theta, "leak")
                else:
                    if passing:
                        bump(theta, "listed")
                    if any(
                        gs.doc_id(h.local_id, h.number, h.jurisdiction) in relevant for h in passing
                    ):
                        bump(theta, "recalled")

    table: list[dict[str, Any]] = []
    for theta in GRID:
        c = counts[theta]
        table.append(
            {
                "min_term_coverage": theta,
                "answerable_listed": c.get("listed", 0) / answerable,
                "answerable_recalled": c.get("recalled", 0) / answerable,
                "negative_leak": c.get("leak", 0) / negative,
            }
        )
    meta = {
        "subset": args.subset,
        "k": args.k,
        "floors": {"lexical": args.lexical_floor, "semantic": args.semantic_floor},
        "answerable": answerable,
        "negative": negative,
        "golden_sha256": golden_sha,
        "qrels_sha256": qrels_doc["sha256"],
        "split_sha256": split_doc["sha256"],
        "snapshots": [
            {k: s[k] for k in ("feed", "data_date", "sha256")} for s in gre_snapshots(settings.dsn)
        ],
    }
    args.out.with_suffix(".json").write_text(
        json.dumps({"meta": meta, "grid": table}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    lines = [
        f"# 列表覆蓋率門檻（{args.subset}，前 {args.k} 條）",
        "",
        f"可答 {answerable} 題、無答案 {negative} 題。",
        "",
        "| 覆蓋率門檻 | 可答題：列得出 | 可答題：列出的有相關條 | 無答案題：誤放 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {t['min_term_coverage']:.2f} | {t['answerable_listed']:.3f} | "
        f"{t['answerable_recalled']:.3f} | {t['negative_leak']:.3f} |"
        for t in table
    ]
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def gre_snapshots(dsn: str) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        return gre.snapshots(conn)


if __name__ == "__main__":
    main()
