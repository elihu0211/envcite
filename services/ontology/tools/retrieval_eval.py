"""Retrieval evaluation with LLM-written questions: build the set once, then measure.

    uv run python tools/retrieval_eval.py generate --models models.toml --out eval/retrieval.jsonl
    uv run python tools/retrieval_eval.py run eval/retrieval.jsonl

`generate` draws one in-force article per law (a seeded, repeatable draw), asks a model for a
question only that article answers — reworded, with no law name, article number or copied
phrase — and adds questions from outside the corpus as negatives. A question that shares too
many character pairs with its article is dropped: it would measure the lexical arm against
itself. The set is written once and then kept: regenerating after looking at the scores tunes
the measurement to the system.

`run` searches every question twice, words only and hybrid, and reports recall@k and MRR for the
answerable ones. For each score it also reports the floor that best separates answerable from
out-of-scope questions (by each question's top score): that is what consult's relevance floor is
calibrated from, and it holds only for the score it was derived from.

Database and embedder come from the environment (ENVCITE_ONTOLOGY_DSN, ENVCITE_ONTOLOGY_MODELS);
the question writer from `models.toml`. This script never reads or writes a key itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from envcite_ontology import embedding
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load
from envcite_ontology.store import OntologyStore
from envcite_ontology.verification import config
from envcite_segmenter import Segmenter

#: 題目與條文共有的字元對比例超過此值即丟掉：模型照抄了條文，量到的是字面臂自己
MAX_OVERLAP = 0.5
#: 太短的條文（「本法自公布日施行」之類）問不出只有它能答的問題
MIN_CHARS = 40

QUESTION = """下面是一條臺灣法規的條文。
請寫一個一般民眾或承辦人員實際會問、而且只有這一條能回答的問題。
要求：用自己的話問，不要照抄條文的用語；不要提法規名稱、條號；只輸出問題本身，一行。

條文：
{text}"""

NEGATIVES = """請寫 {n} 個和法律、法規、行政管制完全無關的日常問題（例如料理、運動、旅遊、程式設計、
天文、音樂），每行一個，不要編號，不要空行。"""

_SAMPLE = """
    SELECT r.local_id, corpus.article_number(a.path), corpus.article_text(a.version_id)
      FROM corpus.legal_resource AS r
     CROSS JOIN LATERAL (
           SELECT s.path, s.version_id
             FROM corpus.provisions_at(r.id, 'zho', %(on)s) AS s
            WHERE s.provision_type = 'article' AND s.status = 'in_force'
            ORDER BY md5(%(seed)s || r.id || s.path)
            LIMIT 1
     ) AS a
     WHERE r.repealed_on IS NULL
     ORDER BY md5(%(seed)s || r.id)
     LIMIT %(limit)s
"""


class Writer(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, prompt: str) -> str: ...


class Embeds(Protocol):
    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def overlap(question: str, text: str) -> float:
    """題目的字元對有多少比例出現在條文裡。"""
    pairs = {question[i : i + 2] for i in range(len(question) - 1)}
    pairs = {p for p in pairs if not any(c in "，。？、 「」" for c in p)}
    if not pairs:
        return 0.0
    return sum(p in text for p in pairs) / len(pairs)


def generate(
    conn: psycopg.Connection[Any],
    writer: Writer,
    *,
    count: int,
    negatives: int,
    seed: str,
    on: date,
) -> list[dict[str, Any]]:
    """寫出題目；回傳的每一題都已過濾（照抄、空答、太長）。"""
    rows = conn.execute(_SAMPLE, {"on": on, "seed": seed, "limit": count * 3}).fetchall()
    items: list[dict[str, Any]] = []
    for law_id, number, text in rows:
        if len(items) >= count:
            break
        text = str(text or "")
        if len(text) < MIN_CHARS or "刪除" in text[:10]:
            continue
        question = writer.complete(QUESTION.format(text=text)).strip().splitlines()[0].strip()
        ratio = overlap(question, text)
        if not question or len(question) > 120 or ratio > MAX_OVERLAP:
            continue
        items.append(
            {
                "id": f"a{len(items) + 1:04d}",
                "kind": "answerable",
                "question": question,
                "law_id": str(law_id),
                "article": str(number),
                "overlap": round(ratio, 3),
                "generator": writer.name,
            }
        )
    lines = writer.complete(NEGATIVES.format(n=negatives)).splitlines()
    for line in [x.strip() for x in lines if x.strip()][:negatives]:
        items.append(
            {
                "id": f"n{sum(i['kind'] == 'out_of_scope' for i in items) + 1:04d}",
                "kind": "out_of_scope",
                "question": line,
                "generator": writer.name,
            }
        )
    return items


@dataclass(frozen=True)
class Outcome:
    rank: int | None  # 正確條文的名次（1 起算），沒找到為 None
    top_lexical: float
    top_semantic: float | None


def search_all(
    store: OntologyStore,
    segmenter: Segmenter,
    items: Sequence[dict[str, Any]],
    embedder: Embeds | None,
    *,
    k: int,
    on: str,
) -> list[Outcome]:
    out = []
    for item in items:
        vector = embedder.embed([item["question"]])[0] if embedder else None
        hits = store.search(
            segmenter.to_tsvector_input(item["question"]),
            tokenizer=segmenter.tokenizer_id,
            as_of=on,
            limit=k,
            embedding=vector,
            model=embedder.model if embedder else "",
        )
        rank = next(
            (
                i
                for i, h in enumerate(hits, 1)
                if (h.law_id, h.number) == (item.get("law_id"), item.get("article"))
            ),
            None,
        )
        semantic = [h.semantic_similarity for h in hits if h.semantic_similarity is not None]
        out.append(
            Outcome(
                rank, max((h.lexical_score for h in hits), default=0.0), max(semantic, default=None)
            )
        )
    return out


def recall(outcomes: Sequence[Outcome], k: int) -> float:
    return sum(o.rank is not None and o.rank <= k for o in outcomes) / max(len(outcomes), 1)


def mrr(outcomes: Sequence[Outcome]) -> float:
    return sum(1 / o.rank for o in outcomes if o.rank) / max(len(outcomes), 1)


def best_floor(
    positives: Sequence[float], negatives: Sequence[float]
) -> tuple[float, float, float]:
    """把正例與反例分得最開的下界（Youden 指數最大）：回傳（下界, 正例通過率, 反例通過率）。"""
    best = (0.0, 1.0, 1.0)
    best_j = -1.0
    for floor in sorted({*positives, *negatives}):
        tpr = sum(p >= floor for p in positives) / max(len(positives), 1)
        fpr = sum(n >= floor for n in negatives) / max(len(negatives), 1)
        if tpr - fpr > best_j:
            best, best_j = (floor, tpr, fpr), tpr - fpr
    return best


def report(
    items: Sequence[dict[str, Any]],
    lexical: Sequence[Outcome],
    hybrid: Sequence[Outcome] | None,
) -> str:
    answerable = [i for i, item in enumerate(items) if item["kind"] == "answerable"]
    scope = [i for i, item in enumerate(items) if item["kind"] == "out_of_scope"]
    lines = [
        f"題數：可答 {len(answerable)}、範圍外 {len(scope)}",
        "",
        "| 檢索 | recall@1 | recall@5 | recall@10 | MRR |",
        "|---|---|---|---|---|",
    ]
    for name, outs in (("字面臂", lexical), ("混合", hybrid)):
        if outs is None:
            continue
        a = [outs[i] for i in answerable]
        row = (recall(a, 1), recall(a, 5), recall(a, 10), mrr(a))
        lines.append(f"| {name} | " + " | ".join(f"{x:.3f}" for x in row) + " |")
    lines += ["", "| 分數 | 下界 | 可答通過 | 範圍外通過 |", "|---|---|---|---|"]
    floor, tpr, fpr = best_floor(
        [lexical[i].top_lexical for i in answerable], [lexical[i].top_lexical for i in scope]
    )
    lines.append(f"| lexical_score | {floor:.3f} | {tpr:.3f} | {fpr:.3f} |")
    if hybrid is not None:
        pos = [s for i in answerable if (s := hybrid[i].top_semantic) is not None]
        neg = [s for i in scope if (s := hybrid[i].top_semantic) is not None]
        if pos and neg:
            floor, tpr, fpr = best_floor(pos, neg)
            lines.append(f"| semantic_similarity | {floor:.3f} | {tpr:.3f} | {fpr:.3f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="retrieval_eval")
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--models", type=Path, required=True)
    g.add_argument(
        "--model", default="", help="models.toml 裡的模型名稱（家族:模型）；預設為抽取者"
    )
    g.add_argument("--count", type=int, default=300)
    g.add_argument("--negatives", type=int, default=60)
    g.add_argument("--seed", default="envcite-retrieval-1")
    g.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("dataset", type=Path)
    r.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    db = load(DatabaseSettings)
    on = date.today()
    if args.command == "generate":
        models = config.load(args.models)
        writer = next((m for m in models.everyone if m.name == args.model), models.extractor)
        with psycopg.connect(db.dsn) as conn:
            items = generate(
                conn, writer, count=args.count, negatives=args.negatives, seed=args.seed, on=on
            )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8"
        )
        print(f"{len(items)} 題寫入 {args.out}")
        return
    items = [
        json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line
    ]
    embedder = embedding.from_settings(load(ModelsSettings), timeout=30.0)
    with OntologyStore.from_dsn(db.dsn) as store:
        segmenter = Segmenter(store.lexicon())
        lexical = search_all(store, segmenter, items, None, k=args.k, on=on.isoformat())
        hybrid = (
            search_all(store, segmenter, items, embedder, k=args.k, on=on.isoformat())
            if embedder
            else None
        )
    print(report(items, lexical, hybrid))


if __name__ == "__main__":
    main()
