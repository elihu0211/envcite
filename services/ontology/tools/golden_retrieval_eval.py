"""Retrieval recall against an external golden question set (read-only), with provenance.

    uv run python tools/golden_retrieval_eval.py <golden.yaml> --out eval/golden-retrieval-<date>

Each question lists the articles that answer it (`citation_allowlist`: law name, article number,
sometimes a `<pcode>-art-<n>` id). They are mapped to this corpus — law name to pcode by exact
`legal_expression.title` (an id's pcode wins), article number to `corpus.provision.article_path` —
and the question is searched with the same `OntologyStore.search` the service uses. A hit is any
expected article within the top k. A question none of whose articles map is `unmapped`, listed
with the reason and left out of the denominator; it is never scored as a miss. The negative bucket
has no recall: only each question's top score is recorded, for calibrating a refusal floor.

The measurement is retrieval only (the pool_recall counterpart); nothing here judges an answer.
The method is fixed: it does not change with the scores. Every number in the output carries the
commit, the corpus snapshots, the question file's sha256 and the retrieval settings that made it.

Database and embedder come from the environment (ONTOLOGY_DSN, ONTOLOGY_MODELS); with no embedding
declared only the lexical arm runs and the result says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieval_eval import Embeds, Outcome, mrr, recall

from envcite_ontology import embedding
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load
from envcite_ontology.store import OntologyStore
from envcite_segmenter import Segmenter

KS = (1, 5, 10, 20, 50)
BUCKETS = ("exact_id", "paraphrase", "multi_hop")
NEGATIVE = "negative"

_ARTICLE = re.compile(r"^第\s*(\d+)(?:\s*條之\s*(\d+)|\s*-\s*(\d+)\s*條|\s*條)")
_ID = re.compile(r"^(?P<pcode>[A-Z]\d{7})-art-(?P<number>\d+(?:-\d+)?)$")


@dataclass(frozen=True)
class Expected:
    statute: str
    article: str
    article_id: str | None = None


@dataclass(frozen=True)
class Provision:
    pcode: str
    number: str  # corpus.article_number 的形式：「68」「15-1」
    article_path: str
    title: str


@dataclass(frozen=True)
class Result:
    id: str
    bucket: str
    question: str
    expected: list[dict[str, str | None]]
    mapped: list[dict[str, str]] = field(default_factory=list)
    unmapped_reasons: list[str] = field(default_factory=list)
    status: str = "scored"  # scored | unmapped | negative
    rank: int | None = None
    hit_article: str | None = None
    top_lexical: float | None = None
    top_semantic: float | None = None


def article_number(text: str | None) -> str | None:
    """「第68條」→「68」、「第15條之1」「第15-1條」→「15-1」；「第38條第2項」取條，項款不入。"""
    m = _ARTICLE.match((text or "").strip())
    if not m:
        return None
    major, sub_a, sub_b = m.groups()
    sub = sub_a or sub_b
    return f"{int(major)}-{int(sub)}" if sub else str(int(major))


def expected_of(item: dict[str, Any]) -> list[Expected]:
    """期望條文；`alternatives`（任一條即可）攤平，命中定義本就是任一條。"""
    out: list[Expected] = []

    def add(entry: dict[str, Any]) -> None:
        if "alternatives" in entry:
            for alt in entry["alternatives"]:
                add(alt)
            return
        out.append(
            Expected(
                str(entry.get("statute") or ""),
                str(entry.get("article") or ""),
                entry.get("article_id"),
            )
        )

    for entry in item.get("citation_allowlist") or []:
        add(entry)
    return out


def map_expected(
    exp: Expected, by_title: dict[str, set[str]], provisions: dict[tuple[str, str], Provision]
) -> tuple[Provision | None, str]:
    """（對應的條，沒有時的原因）。有 article_id 時其 pcode 與條號為準。"""
    if exp.article_id:
        m = _ID.match(exp.article_id)
        if not m:
            return None, f"article_id 格式不合：{exp.article_id}"
        pcode, number = m["pcode"], m["number"]
    else:
        candidates = by_title.get(exp.statute, set())
        if not candidates:
            return None, f"法規名稱在資料庫沒有完全相同的標題：{exp.statute}"
        if len(candidates) > 1:
            return None, f"法規名稱對應多個 pcode：{exp.statute} → {sorted(candidates)}"
        pcode = next(iter(candidates))
        parsed = article_number(exp.article)
        if parsed is None:
            return None, f"條號無法解析：{exp.article!r}"
        number = parsed
    found = provisions.get((pcode, number))
    if found is None:
        return None, f"資料庫沒有這一條：{pcode} 第{number}條"
    return found, ""


def load_corpus(
    conn: psycopg.Connection[Any],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], Provision]]:
    """標題 → pcode 集合（取各法規所有版本的名稱）；（pcode, 條號）→ 條。"""
    by_title: dict[str, set[str]] = defaultdict(set)
    for pcode, title in conn.execute(
        "SELECT r.local_id, e.title FROM corpus.legal_resource AS r"
        " JOIN corpus.legal_expression AS e ON e.resource_id = r.id"
        " WHERE r.id_scheme = 'moj_pcode'"
    ):
        by_title[str(title)].add(str(pcode))
    titles = {
        str(pcode): str(title)
        for pcode, title in conn.execute(
            "SELECT DISTINCT ON (r.id) r.local_id, e.title FROM corpus.legal_resource AS r"
            " JOIN corpus.legal_expression AS e ON e.resource_id = r.id"
            " WHERE r.id_scheme = 'moj_pcode' ORDER BY r.id, e.version_date DESC"
        )
    }
    provisions: dict[tuple[str, str], Provision] = {}
    for pcode, number, path in conn.execute(
        "SELECT r.local_id, corpus.article_number(p.article_path), p.article_path"
        "  FROM corpus.provision AS p JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE r.id_scheme = 'moj_pcode' AND p.provision_type = 'article'"
        "   AND p.article_path IS NOT NULL"
    ):
        provisions[(str(pcode), str(number))] = Provision(
            str(pcode), str(number), str(path), titles.get(str(pcode), "")
        )
    return dict(by_title), provisions


def resolve(
    items: Iterable[dict[str, Any]],
    by_title: dict[str, set[str]],
    provisions: dict[tuple[str, str], Provision],
) -> list[Result]:
    results = []
    for item in items:
        exp = expected_of(item)
        mapped: list[dict[str, str]] = []
        reasons: list[str] = []
        seen: set[tuple[str, str]] = set()
        for e in exp:
            prov, why = map_expected(e, by_title, provisions)
            if prov is None:
                reasons.append(why)
            elif (prov.pcode, prov.number) not in seen:
                seen.add((prov.pcode, prov.number))
                mapped.append(
                    {
                        "pcode": prov.pcode,
                        "title": prov.title,
                        "article_path": prov.article_path,
                        "number": prov.number,
                    }
                )
        bucket = str(item["bucket"])
        if bucket == NEGATIVE:
            status = "negative"
        elif mapped:
            status = "scored"
        else:
            status = "unmapped"
            if not exp:
                reasons.append("題目沒有期望條文")
        results.append(
            Result(
                id=str(item["id"]),
                bucket=bucket,
                question=str(item["question"]),
                expected=[asdict(e) for e in exp],
                mapped=mapped,
                unmapped_reasons=reasons,
                status=status,
            )
        )
    return results


def search_all(
    store: OntologyStore,
    segmenter: Segmenter,
    results: Sequence[Result],
    embedder: Embeds | None,
    *,
    k: int,
    on: str,
) -> list[Result]:
    """逐題檢索前 k 條，回傳填了名次與最高分的新 Result；negative 只記最高分。"""
    out = []
    for r in results:
        if r.status == "unmapped":
            out.append(r)
            continue
        vector = embedder.embed([r.question])[0] if embedder else None
        hits = store.search(
            segmenter.to_tsvector_input(r.question),
            tokenizer=segmenter.tokenizer_id,
            as_of=on,
            limit=k,
            embedding=vector,
            model=embedder.model if embedder else "",
        )
        semantic = [h.semantic_similarity for h in hits if h.semantic_similarity is not None]
        rank, hit_article = None, None
        if r.status == "scored":
            wanted = {(m["pcode"], m["number"]) for m in r.mapped}
            for i, h in enumerate(hits, 1):
                if (h.law_id, h.number) in wanted:
                    rank, hit_article = i, f"{h.law_id} 第{h.number}條"
                    break
        out.append(
            replace(
                r,
                rank=rank,
                hit_article=hit_article,
                top_lexical=max((h.lexical_score for h in hits), default=0.0),
                top_semantic=max(semantic, default=None),
            )
        )
    return out


def _distribution(values: Sequence[float | None]) -> dict[str, float | int] | None:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None

    def pick(q: float) -> float:
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    return {
        "n": len(xs),
        "min": xs[0],
        "p25": pick(0.25),
        "median": pick(0.5),
        "p75": pick(0.75),
        "max": xs[-1],
    }


def summarize(results: Sequence[Result]) -> dict[str, Any]:
    def block(rows: Sequence[Result]) -> dict[str, Any]:
        outs = [Outcome(r.rank, r.top_lexical or 0.0, r.top_semantic) for r in rows]
        return {
            "n": len(rows),
            **{f"recall@{k}": round(recall(outs, k), 4) for k in KS},
            "mrr": round(mrr(outs), 4),
        }

    by_bucket: dict[str, Any] = {}
    for b in BUCKETS:
        rows = [r for r in results if r.bucket == b]
        by_bucket[b] = {
            "questions": len(rows),
            "unmapped": sum(r.status == "unmapped" for r in rows),
            "partially_mapped": sum(
                r.status == "scored" and bool(r.unmapped_reasons) for r in rows
            ),
            **block([r for r in rows if r.status == "scored"]),
        }
    neg = [r for r in results if r.bucket == NEGATIVE]
    return {
        "by_bucket": by_bucket,
        "overall": {
            "questions": sum(r.bucket in BUCKETS for r in results),
            "unmapped": sum(r.status == "unmapped" for r in results),
            "partially_mapped": sum(v["partially_mapped"] for v in by_bucket.values()),
            **block([r for r in results if r.status == "scored"]),
        },
        "negative": {
            "n": len(neg),
            "top_lexical": _distribution([r.top_lexical for r in neg]),
            "top_semantic": _distribution([r.top_semantic for r in neg]),
        },
    }


def markdown(meta: dict[str, Any], arm: str, s: dict[str, Any]) -> str:
    g, r, c = meta["golden"], meta["retrieval"], meta["corpus"]
    lines = [
        f"## {arm} 臂",
        "",
        "| 桶 | 題數 | unmapped | 部分對應 | 計分 n | "
        + " | ".join(f"R@{k}" for k in KS)
        + " | MRR |",
        "|---|---|---|---|---|" + "---|" * (len(KS) + 1),
    ]
    rows = [(b, s["by_bucket"][b]) for b in BUCKETS] + [("總計", s["overall"])]
    for name, x in rows:
        lines.append(
            f"| {name} | {x['questions']} | {x['unmapped']} | {x['partially_mapped']} | {x['n']} | "
            + " | ".join(f"{x[f'recall@{k}']:.3f}" for k in KS)
            + f" | {x['mrr']:.3f} |"
        )
    n = s["negative"]
    lines += ["", f"negative 桶 {n['n']} 題的最高分（不算召回）：", ""]
    for key in ("top_lexical", "top_semantic"):
        d = n[key]
        lines.append(
            f"- {key}：" + (", ".join(f"{k}={v:.4g}" for k, v in d.items()) if d else "無")
        )
    header = [
        "# 黃金題庫條文檢索召回（envcite）",
        "",
        f"- 執行：{meta['run_at']}（as_of {meta['as_of']}）；commit `{meta['envcite_commit']}`"
        + ("（工作樹有未提交變更）" if meta["git_dirty"] else ""),
        f"- 題庫：`{g['path']}`，sha256 `{g['sha256']}`，{g['items']} 題",
        "- 語料快照："
        + "；".join(f"{x['feed']} data_date {x['data_date']}" for x in meta["snapshots"]),
        f"- 語料：{c['legal_resources']} 部法規、{c['provisions']} 個條文單元、"
        f"{c['passages']} 筆 passage（其中有嵌入 {c['passages_with_embedding']}）",
        f"- 檢索：{r['arms']}；分詞器 `{r['tokenizer']}`；k = {r['k']}",
        "- 命中：任一期望條文在前 k 條；unmapped 不計入分母，原因逐題列在 JSON；"
        "部分對應的題只以對得到的條文計。僅檢索層，不含答對率。",
        "",
    ]
    return "\n".join(header + lines) + "\n"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=Path(__file__).parent
    ).stdout.strip()


def snapshots(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT DISTINCT ON (feed) feed, data_date, encode(sha256, 'hex'), fetched_at"
        "  FROM source.snapshot ORDER BY feed, data_date DESC, id DESC"
    ).fetchall()
    return [
        {"feed": f, "data_date": d.isoformat(), "sha256": h, "fetched_at": t.isoformat()}
        for f, d, h, t in rows
    ]


def corpus_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM corpus.legal_resource),"
        " (SELECT count(*) FROM corpus.provision),"
        " (SELECT count(*) FROM retrieval.passage),"
        " (SELECT count(embedding) FROM retrieval.passage)"
    ).fetchone()
    assert row is not None
    keys = ("legal_resources", "provisions", "passages", "passages_with_embedding")
    return dict(zip(keys, (int(x) for x in row), strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="golden_retrieval_eval")
    parser.add_argument("golden", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="輸出檔前綴（寫 .json 與 .md）")
    parser.add_argument("--k", type=int, default=max(KS))
    args = parser.parse_args()
    if args.k < max(KS):
        parser.error(f"--k 至少 {max(KS)}（recall@{max(KS)} 需要）")

    raw = args.golden.read_bytes()
    items = yaml.safe_load(raw)["items"]
    db = load(DatabaseSettings)
    embedder = embedding.from_settings(load(ModelsSettings), timeout=30.0, max_retries=3)
    on = date.today().isoformat()
    with psycopg.connect(db.ontology_dsn) as conn:
        by_title, provisions = load_corpus(conn)
        snaps = snapshots(conn)
        counts = corpus_counts(conn)
    resolved = resolve(items, by_title, provisions)
    with OntologyStore.from_dsn(db.ontology_dsn) as store:
        segmenter = Segmenter(store.lexicon())
        arms = {"lexical": search_all(store, segmenter, resolved, None, k=args.k, on=on)}
        if embedder:
            arms["hybrid"] = search_all(store, segmenter, resolved, embedder, k=args.k, on=on)
        tokenizer = segmenter.tokenizer_id

    meta = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": on,
        "envcite_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "..")),
        "golden": {
            "path": str(args.golden.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "items": len(items),
        },
        "snapshots": snaps,
        "corpus": counts,
        "retrieval": {
            "arms": "字面 + 混合" if embedder else "僅字面（未設定嵌入端點）",
            "passages_with_embedding": counts["passages_with_embedding"],
            "tokenizer": tokenizer,
            "k": args.k,
            "embedding_model": embedder.model if embedder else None,
        },
    }
    summaries = {name: summarize(rs) for name, rs in arms.items()}
    doc = {
        "meta": meta,
        "arms": {
            name: {"summary": summaries[name], "results": [asdict(r) for r in rs]}
            for name, rs in arms.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    json_path = args.out.parent / (args.out.name + ".json")
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    parts = [markdown(meta, name, s) for name, s in summaries.items()]
    md = parts[0] + "".join(p[p.index("## ") :] for p in parts[1:])
    (args.out.parent / (args.out.name + ".md")).write_text(md, encoding="utf-8")
    print(md)
    print(f"結果：{json_path}")


if __name__ == "__main__":
    main()
