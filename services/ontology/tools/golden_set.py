"""The golden test collection: qrels, runs, the dev/test split, the judgement pool and its holes.

    uv run python tools/golden_set.py split <golden.yaml> --out eval/golden-split.json
    uv run python tools/golden_set.py pool <golden.yaml> --qrels eval/golden-qrels.json \\
        --run <report>.hybrid.run.json [--run …] --out <outside the repo>/pool.yaml
    uv run python tools/golden_set.py judge --qrels eval/golden-qrels.json \\
        --judgements <filled pool.yaml> --out eval/golden-qrels.json

A qrels document maps each answerable question to the articles judged for it (`<pcode>-art-<n>`,
relevance 1 or 0); a judged 0 is no longer a hole. Negative questions are never in it: they measure
refusal. A run maps each question to the articles a retrieval variant ranked. Metrics come from
these two alone (ranx), so a run made before a judgement can be scored again after it.

Every document written here carries `sha256`, the hash of its own canonical JSON without that
field, and the hashes of what it was made from, so a report can name exactly which versions made
its numbers.

`split` assigns every question (negatives included) to `dev` or `test`, stratified by bucket with a
fixed seed. Floors, fusion and candidate pools are chosen on dev; test is reported once, at the end.

`pool` lists the holes of the given runs: articles in a question's top 10 that the qrels has no
judgement for, with the question and the article's full text, for a person to judge. The file holds
question text (the golden set is external): keep it outside the repository. `judge` merges a filled
pool back into qrels. Only `relevance` is read; a model's pre-screen goes in `prescreen` and is
never imported. The file must name its `judge` and `date`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml

# ranx 的報表模組在 Python 3.14 編譯時對其中的 LaTeX 字串發 SyntaxWarning，與這裡的計算無關。
with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    from ranx import Qrels as RQrels
    from ranx import Run
    from ranx import compare as rx_compare
    from ranx import evaluate as rx_evaluate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from envcite_ontology.settings import DatabaseSettings, load

NEGATIVE = "negative"
HOLE_DEPTH = 10
DOC = re.compile(r"^(?P<pcode>[A-Z]\d{7})-art-(?P<number>\d+(?:-\d+)?)$")

Qrels = dict[str, dict[str, int]]
Ranking = dict[str, list[str]]


def doc_id(pcode: str, number: str) -> str:
    """條文代號：`O0020001-art-15-1`（題庫 `article_id` 的形式）。"""
    return f"{pcode}-art-{number}"


def parse_doc(doc: str) -> tuple[str, str]:
    m = DOC.match(doc)
    if not m:
        raise ValueError(f"條文代號格式不合：{doc}")
    return m["pcode"], m["number"]


def digest(doc: Mapping[str, Any]) -> str:
    body = {k: v for k, v in doc.items() if k != "sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal(doc: dict[str, Any]) -> dict[str, Any]:
    return {**doc, "sha256": digest(doc)}


def write(path: Path, doc: dict[str, Any]) -> dict[str, Any]:
    sealed = seal(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sealed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return sealed


def read(path: Path) -> dict[str, Any]:
    """讀一份封存的文件；內容與 `sha256` 不符就停。"""
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("sha256") != digest(doc):
        raise SystemExit(f"{path}: sha256 與內容不符（被手改過？）")
    return doc


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_golden(doc: Mapping[str, Any], golden_sha256: str, what: str) -> None:
    if doc["golden_sha256"] != golden_sha256:
        raise SystemExit(f"{what} 不是由這份題庫產生的（golden sha256 不同）")


def relevant(qrels: Qrels) -> Qrels:
    """只留判為相關的條；ranx 以 relevance > 0 為相關，0 只表示已判定。"""
    return {q: {d: r for d, r in docs.items() if r > 0} for q, docs in qrels.items()}


def split(
    items: Sequence[Mapping[str, Any]], *, seed: str, test_fraction: float
) -> dict[str, list[str]]:
    """依桶分層：每桶的題號排序後以（種子, 桶）洗牌，前 round(n × 比例) 題進測試集。"""
    by_bucket: dict[str, list[str]] = {}
    for item in items:
        by_bucket.setdefault(str(item["bucket"]), []).append(str(item["id"]))
    dev: list[str] = []
    test: list[str] = []
    for bucket, ids in sorted(by_bucket.items()):
        ids = sorted(ids)
        random.Random(f"{seed}/{bucket}").shuffle(ids)
        n = round(len(ids) * test_fraction)
        test += ids[:n]
        dev += ids[n:]
    return {"dev": sorted(dev), "test": sorted(test)}


def subset_ids(split_doc: Mapping[str, Any], subset: str) -> frozenset[str]:
    if subset not in ("dev", "test"):
        raise SystemExit(f"子集只有 dev 與 test：{subset}")
    return frozenset(split_doc[subset])


def run_scores(ranking: Ranking) -> dict[str, dict[str, float]]:
    """名次轉成 ranx 的分數：第 i 名得 n − i + 1，順序與檢索輸出完全相同、沒有同分。"""
    return {q: {d: float(len(docs) - i) for i, d in enumerate(docs)} for q, docs in ranking.items()}


def holes(qrels: Qrels, ranking: Ranking, depth: int = HOLE_DEPTH) -> dict[str, list[str]]:
    """每題前 depth 條中沒有判定（不論相關與否）的條。"""
    return {
        q: [d for d in docs[:depth] if d not in qrels.get(q, {})] for q, docs in ranking.items()
    }


def hole_rate(qrels: Qrels, ranking: Ranking, depth: int = HOLE_DEPTH) -> float | None:
    """Hole@depth：所有題前 depth 條中沒有判定的比例；沒有任何結果時為 None。"""
    slots = sum(len(docs[:depth]) for docs in ranking.values())
    if not slots:
        return None
    return sum(map(len, holes(qrels, ranking, depth).values())) / slots


def evaluate(qrels: Qrels, ranking: Ranking, metrics: Sequence[str]) -> dict[str, float]:
    """以 ranx 算各指標的平均；只算 qrels 裡有的題，run 沒交的題以 0 計。"""
    q = {k: v for k, v in relevant(qrels).items() if v}
    run = {k: v for k, v in run_scores(ranking).items() if k in q and v}
    if not q:
        return dict.fromkeys(metrics, 0.0)
    got = rx_evaluate(RQrels(q), Run(run), list(metrics), make_comparable=True)
    if isinstance(got, float):
        return {metrics[0]: float(got)}
    return {m: float(got[m]) for m in metrics}


def compare(
    qrels: Qrels,
    rankings: Mapping[str, Ranking],
    metrics: Sequence[str],
    *,
    permutations: int = 10_000,
    seed: int = 0,
) -> dict[str, Any]:
    """成對 Fisher 隨機化檢定（ranx `compare`）：各 run 的平均與兩兩的 p 值。"""
    q = {k: v for k, v in relevant(qrels).items() if v}
    runs = [
        Run({k: v for k, v in run_scores(r).items() if k in q and v}, name=name)
        for name, r in rankings.items()
    ]
    report = rx_compare(
        RQrels(q),
        runs,
        list(metrics),
        stat_test="fisher",
        n_permutations=permutations,
        random_seed=seed,
        make_comparable=True,
    )
    out: dict[str, Any] = report.to_dict()
    return out


def pool_rows(
    qrels: Qrels, rankings: Iterable[Ranking], depth: int = HOLE_DEPTH
) -> list[tuple[str, str]]:
    """各 run 的洞合併去重：（題號, 條文代號），依題號、條文代號排序。"""
    pairs: set[tuple[str, str]] = set()
    for ranking in rankings:
        for q, docs in holes(qrels, ranking, depth).items():
            if q in qrels:
                pairs.update((q, d) for d in docs)
    return sorted(pairs)


def merge_judgements(qrels: Qrels, judged: Mapping[str, Any]) -> tuple[Qrels, int]:
    """把填好的判定併進 qrels；回傳（新 qrels, 新增的判定數）。

    只讀 `relevance`（0 或 1）；留空的列仍是洞。`prescreen`（模型預篩）一律不讀。
    與 qrels 已有判定矛盾的列整份拒絕。
    """
    if not str(judged.get("judge") or "").strip():
        raise ValueError("判定檔沒有寫判定者（judge）")
    try:
        date.fromisoformat(str(judged.get("date")))
    except ValueError:
        raise ValueError(f"判定檔的日期不是 ISO 8601：{judged.get('date')!r}") from None
    out = {q: dict(docs) for q, docs in qrels.items()}
    added = 0
    problems: list[str] = []
    for row in judged.get("items") or []:
        q, d, rel = str(row["qid"]), str(row["doc"]), row.get("relevance")
        if rel is None:
            continue
        if rel not in (0, 1) or isinstance(rel, bool):
            problems.append(f"{q} {d}：relevance 只能是 0 或 1，得到 {rel!r}")
            continue
        if q not in out:
            problems.append(f"{q}：qrels 沒有這一題（無答案題與 unmapped 題不判）")
            continue
        parse_doc(d)
        have = out[q].get(d)
        if have is None:
            out[q][d] = int(rel)
            added += 1
        elif have != rel:
            problems.append(f"{q} {d}：qrels 已判為 {have}，判定檔為 {rel}")
    if problems:
        raise ValueError("判定檔有問題，整份不併入：\n" + "\n".join(problems))
    return out, added


def article_texts(
    conn: psycopg.Connection[Any], docs: Iterable[str], on: str
) -> dict[str, tuple[str, str]]:
    """條文代號 → （法規名稱, 該日有效的條文全文）。"""
    wanted = {parse_doc(d) for d in docs}
    pcodes = sorted({p for p, _ in wanted})
    out: dict[str, tuple[str, str]] = {}
    for pcode, title, number, text in conn.execute(
        "SELECT r.local_id, e.title, corpus.article_number(s.path),"
        "       corpus.article_text(s.version_id)"
        "  FROM corpus.legal_resource AS r"
        "  JOIN corpus.legal_expression AS e"
        "    ON e.resource_id = r.id AND e.language = 'zho' AND e.text_period @> %(on)s::date"
        " CROSS JOIN LATERAL corpus.provisions_at(r.id, 'zho', %(on)s::date) AS s"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(%(pcodes)s)"
        "   AND s.provision_type = 'article'",
        {"on": on, "pcodes": pcodes},
    ):
        if (str(pcode), str(number)) in wanted:
            out[doc_id(str(pcode), str(number))] = (str(title), str(text or ""))
    return out


def _items(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    items: list[dict[str, Any]] = yaml.safe_load(raw)["items"]
    return items, hashlib.sha256(raw).hexdigest()


def cmd_split(args: argparse.Namespace) -> None:
    items, golden_sha = _items(args.golden)
    parts = split(items, seed=args.seed, test_fraction=args.test_fraction)
    bucket = {str(i["id"]): str(i["bucket"]) for i in items}
    counts = {
        name: {b: sum(bucket[q] == b for q in ids) for b in sorted(set(bucket.values()))}
        for name, ids in parts.items()
    }
    doc = write(
        args.out,
        {
            "golden_sha256": golden_sha,
            "seed": args.seed,
            "test_fraction": args.test_fraction,
            "method": "依桶分層；每桶題號排序後以 random.Random('<seed>/<bucket>') 洗牌，"
            "前 round(n × test_fraction) 題為 test",
            "counts": counts,
            **parts,
        },
    )
    print(f"dev {len(parts['dev'])} 題、test {len(parts['test'])} 題；{counts}")
    print(f"{args.out}：sha256 {doc['sha256']}")


def cmd_pool(args: argparse.Namespace) -> None:
    items, golden_sha = _items(args.golden)
    qrels_doc = read(args.qrels)
    require_golden(qrels_doc, golden_sha, str(args.qrels))
    runs = [read(p) for p in args.run]
    for p, r in zip(args.run, runs, strict=True):
        require_golden(r, golden_sha, str(p))
    qrels: Qrels = qrels_doc["qrels"]
    pairs = pool_rows(qrels, [r["ranking"] for r in runs], args.depth)
    questions = {str(i["id"]): i for i in items}
    db = load(DatabaseSettings)
    with psycopg.connect(db.dsn) as conn:
        conn.read_only = True
        texts = article_texts(conn, {d for _, d in pairs}, date.today().isoformat())
    rows = []
    for q, d in pairs:
        title, text = texts.get(d, ("", ""))
        _, number = parse_doc(d)
        rows.append(
            {
                "qid": q,
                "bucket": questions[q]["bucket"],
                "question": str(questions[q]["question"]).strip(),
                "doc": d,
                "article": f"{title} 第{number}條",
                "text": text,
                "relevance": None,
                "prescreen": None,
            }
        )
    doc = {
        "judge": None,
        "date": None,
        "instructions": "每列讀題目與條文全文，條文能回答（或是回答所需的一部分）填 relevance: 1，"
        "否則填 0；判不了就留空（仍是洞）。prescreen 是模型預篩，只供參考，不會匯入。"
        "填完寫上 judge（判定者）與 date（YYYY-MM-DD），再以 golden_set.py judge 併入 qrels。"
        "本檔含題目全文，不提交進倉庫。",
        "golden_sha256": golden_sha,
        "qrels_sha256": qrels_doc["sha256"],
        "runs": [
            {"path": p.name, "sha256": r["sha256"]} for p, r in zip(args.run, runs, strict=True)
        ],
        "depth": args.depth,
        "items": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{len(rows)} 個待判（{len({q for q, _ in pairs})} 題）：{args.out}")


def cmd_judge(args: argparse.Namespace) -> None:
    qrels_doc = read(args.qrels)
    raw = args.judgements.read_bytes()
    judged = yaml.safe_load(raw)
    if judged.get("golden_sha256") != qrels_doc["golden_sha256"]:
        raise SystemExit("判定檔與 qrels 不是同一份題庫")
    try:
        qrels, added = merge_judgements(qrels_doc["qrels"], judged)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    record = {
        "path": args.judgements.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "judge": str(judged["judge"]).strip(),
        "date": str(judged["date"]),
        "added": added,
    }
    doc = write(
        args.out,
        {**qrels_doc, "qrels": qrels, "judgements": [*qrels_doc["judgements"], record]},
    )
    print(f"併入 {added} 個判定；{args.out}：sha256 {doc['sha256']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="golden_set")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("split", help="開發集與測試集")
    s.add_argument("golden", type=Path)
    s.add_argument("--seed", default="envcite-golden-1")
    s.add_argument("--test-fraction", type=float, default=0.5)
    s.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pool", help="匯出待判清單（各 run 前 10 條的洞）")
    p.add_argument("golden", type=Path)
    p.add_argument("--qrels", type=Path, required=True)
    p.add_argument("--run", type=Path, action="append", required=True)
    p.add_argument("--depth", type=int, default=HOLE_DEPTH)
    p.add_argument("--out", type=Path, required=True)
    j = sub.add_parser("judge", help="把填好的待判清單併入 qrels")
    j.add_argument("--qrels", type=Path, required=True)
    j.add_argument("--judgements", type=Path, required=True)
    j.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    {"split": cmd_split, "pool": cmd_pool, "judge": cmd_judge}[args.command](args)


if __name__ == "__main__":
    main()
