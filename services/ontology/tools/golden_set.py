"""The golden test collection: qrels, runs, the dev/test split, the judgement pool and its holes.

    uv run python tools/golden_set.py split <golden.yaml> --out eval/golden-split.json
    uv run python tools/golden_set.py judge --qrels eval/golden-qrels.json \\
        --judgements eval/golden-judgements-<subset>-<date>.json --out eval/golden-qrels.json

A qrels document maps each answerable question to the articles judged for it (`<pcode>-art-<n>`)
with a graded relevance 0–3 (UMBRELA's scale); an article is relevant from `relevant_from` (2) up,
and a judged 0 or 1 is no longer a hole. The golden set's own expected articles (`upstream`) enter
at relevance 2 and a model's grade can only raise them. Negative questions are never in it: they
measure refusal. A run maps each question to the articles a retrieval variant ranked. Metrics come
from these two alone (ranx): R@k and MRR on relevance ≥ `relevant_from`, nDCG on the grades, so a
run made before a judgement can be scored again after it.

Every document written here carries `sha256`, the hash of its own canonical JSON without that
field, and the hashes of what it was made from, so a report can name exactly which versions made
its numbers.

`split` assigns every question (negatives included) to `dev` or `test`, stratified by bucket with a
fixed seed. Floors, fusion, candidate pools and the judge prompt are chosen on dev; test is reported
once, at the end.

The holes of the runs (articles in a question's top 10 that the qrels has no judgement for) are
judged by models (`relevance_judge.py`); `judge` merges the sealed judgement file back into qrels
and records its hash, judges, date and prompt hash.
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


NEGATIVE = "negative"
HOLE_DEPTH = 10
DOC = re.compile(
    r"^(?:(?P<jurisdiction>[A-Z]{2}-[A-Za-z0-9]+)/)?(?P<local_id>[A-Za-z0-9_.-]+)-art-(?P<number>\d+(?:-\d+)?)$"
)

Qrels = dict[str, dict[str, int]]
Ranking = dict[str, list[str]]


#: 中央法規在 `corpus.legal_resource.jurisdiction` 的值。條文代號對中央省略管轄前綴，
#: 地方法規帶 `<管轄>/`；工具各處都用這個常數，不各自寫字面值。
CENTRAL = "TW-CENTRAL"


def doc_id(local_id: str, number: str, jurisdiction: str = CENTRAL) -> str:
    """條文代號：中央 `O0020001-art-15-1` 或地方 `TW-TPE/01-02-1001-art-5`。"""
    if jurisdiction == CENTRAL:
        return f"{local_id}-art-{number}"
    return f"{jurisdiction}/{local_id}-art-{number}"


def parse_doc(doc: str) -> tuple[str, str]:
    """回傳 (local_id, number)，丟掉管轄；呼叫端只在中央法規上用（查詢另有方案限定）。"""
    m = DOC.match(doc)
    if not m:
        raise ValueError(f"條文代號格式不合：{doc}")
    return m["local_id"], m["number"]


def parse_doc_key(doc: str) -> tuple[str, str, str]:
    """回傳複合鍵 (jurisdiction, local_id, number)；沒有管轄前綴的是中央。"""
    m = DOC.match(doc)
    if not m:
        raise ValueError(f"條文代號格式不合：{doc}")
    return m["jurisdiction"] or CENTRAL, m["local_id"], m["number"]


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


#: UMBRELA 的分級；relevant_from 以上為相關（二元指標與 Cohen's κ 的二元化）
SCALE = (0, 1, 2, 3)
RELEVANT_FROM = 2
#: 題庫給定的期望條文進 qrels 的分數；模型的判定只能把它往上調
UPSTREAM_GRADE = 2


def relevant(qrels: Qrels, *, relevant_from: int) -> Qrels:
    """只留判為相關（≥ relevant_from）的條，值一律為 1；ranx 的二元指標以 > 0 為相關。"""
    return {q: {d: 1 for d, r in docs.items() if r >= relevant_from} for q, docs in qrels.items()}


def graded(qrels: Qrels) -> Qrels:
    """nDCG 用的分級：分數即增益，0 不入。"""
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


def _is_graded(metric: str) -> bool:
    return metric.startswith("ndcg")


def _judged_questions(qrels: Qrels, relevant_from: int) -> Qrels:
    """計分的題：至少有一條相關（≥ relevant_from）。"""
    return {k: v for k, v in relevant(qrels, relevant_from=relevant_from).items() if v}


def evaluate(
    qrels: Qrels, ranking: Ranking, metrics: Sequence[str], *, relevant_from: int
) -> dict[str, float]:
    """以 ranx 算各指標的平均；只算至少有一條相關的題，run 沒交的題以 0 計。

    `ndcg@k` 以分級為增益，其餘指標以 ≥ relevant_from 為相關。
    """
    binary = _judged_questions(qrels, relevant_from)
    if not binary:
        return dict.fromkeys(metrics, 0.0)
    gains = {k: v for k, v in graded(qrels).items() if k in binary}
    run = {k: v for k, v in run_scores(ranking).items() if k in binary and v}
    out: dict[str, float] = {}
    for q, ms in (
        (gains, [m for m in metrics if _is_graded(m)]),
        (binary, [m for m in metrics if not _is_graded(m)]),
    ):
        if not ms:
            continue
        got = rx_evaluate(RQrels(q), Run(run), ms, make_comparable=True)
        out.update(
            {ms[0]: float(got)} if isinstance(got, float) else {m: float(got[m]) for m in ms}
        )
    return {m: out[m] for m in metrics}


def compare(
    qrels: Qrels,
    rankings: Mapping[str, Ranking],
    metrics: Sequence[str],
    *,
    relevant_from: int,
    permutations: int = 10_000,
    seed: int = 0,
) -> dict[str, Any]:
    """成對 Fisher 隨機化檢定（ranx `compare`）：各 run 的平均與兩兩的 p 值。

    回傳 run → {"scores": 指標 → 平均, "comparisons": 另一個 run → 指標 → p}。
    """
    binary = _judged_questions(qrels, relevant_from)
    gains = {k: v for k, v in graded(qrels).items() if k in binary}
    out: dict[str, Any] = {name: {"scores": {}, "comparisons": {}} for name in rankings}
    for q, ms in (
        (gains, [m for m in metrics if _is_graded(m)]),
        (binary, [m for m in metrics if not _is_graded(m)]),
    ):
        if not ms:
            continue
        runs = [
            Run({k: v for k, v in run_scores(r).items() if k in binary and v}, name=name)
            for name, r in rankings.items()
        ]
        report = rx_compare(
            RQrels(q),
            runs,
            ms,
            stat_test="fisher",
            n_permutations=permutations,
            random_seed=seed,
            make_comparable=True,
        ).to_dict()
        for name in rankings:
            out[name]["scores"].update(report[name]["scores"])
            for other, ps in report[name]["comparisons"].items():
                out[name]["comparisons"].setdefault(other, {}).update(ps)
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


def merge_judgements(
    doc: Mapping[str, Any], rows: Iterable[Mapping[str, Any]], tag: str
) -> tuple[Qrels, dict[str, dict[str, str]], int]:
    """把模型判定的分數併進 qrels；回傳（新 qrels, 新 graded_by, 新增的判定數）。

    題庫給定的條（`upstream`）取 max(UPSTREAM_GRADE, 分數)，其餘取分數；`grade` 為 None 的列仍是
    洞。同一條已有模型判定時新值必須相同（不覆寫），題號不在 qrels、分數不在 0–3 都使整份拒絕。
    `graded_by` 記每一條模型判定來自哪份判定檔（`tag`）。
    """
    qrels: Qrels = doc["qrels"]
    upstream = {q: set(ds) for q, ds in doc["upstream"].items()}
    out = {q: dict(docs) for q, docs in qrels.items()}
    by = {q: dict(ds) for q, ds in doc["graded_by"].items()}
    added = 0
    problems: list[str] = []
    for row in rows:
        q, d, grade = str(row["qid"]), str(row["doc"]), row.get("grade")
        if grade is None:
            continue
        if grade not in SCALE or isinstance(grade, bool):
            problems.append(f"{q} {d}：分數只能是 0–3，得到 {grade!r}")
            continue
        if q not in out:
            problems.append(f"{q}：qrels 沒有這一題（無答案題與 unmapped 題不判）")
            continue
        parse_doc(d)
        value = max(UPSTREAM_GRADE, int(grade)) if d in upstream.get(q, set()) else int(grade)
        if d in by.get(q, {}):
            if out[q][d] != value:
                problems.append(f"{q} {d}：qrels 已判為 {out[q][d]}，判定檔為 {value}")
            continue
        if d in out[q] and d not in upstream.get(q, set()):
            problems.append(f"{q} {d}：qrels 已有不是題庫給定、也沒有判定來源的分數")
            continue
        out[q][d] = value
        by.setdefault(q, {})[d] = tag
        added += 1
    if problems:
        raise ValueError("判定檔有問題，整份不併入：\n" + "\n".join(problems))
    return out, by, added


def article_texts(
    conn: psycopg.Connection[Any], docs: Iterable[str], on: str
) -> dict[str, tuple[str, str, str]]:
    """條文代號 → （法規名稱, 該日有效版本的公布或修正日, 條文全文）。"""
    wanted = {parse_doc_key(d) for d in docs}
    jurisdictions = [j for j, _, _ in wanted]
    local_ids = [lid for _, lid, _ in wanted]
    out: dict[str, tuple[str, str, str]] = {}
    for jur, local_id, title, version, number, text in conn.execute(
        "SELECT r.jurisdiction, r.local_id, e.title, e.version_date, corpus.article_number(s.path),"
        "       corpus.article_text(s.version_id)"
        "  FROM unnest(%(jurisdictions)s::text[], %(local_ids)s::text[]) AS w(jur, lid)"
        "  JOIN corpus.legal_resource AS r ON r.jurisdiction = w.jur AND r.local_id = w.lid"
        "  JOIN corpus.legal_expression AS e"
        "    ON e.resource_id = r.id AND e.language = 'zho' AND e.text_period @> %(on)s::date"
        " CROSS JOIN LATERAL corpus.provisions_at(r.id, 'zho', %(on)s::date) AS s"
        " WHERE s.provision_type = 'article'",
        {"on": on, "jurisdictions": jurisdictions, "local_ids": local_ids},
    ):
        j_str = str(jur)
        lid_str = str(local_id)
        num_str = str(number)
        if (j_str, lid_str, num_str) in wanted:
            out[doc_id(lid_str, num_str, j_str)] = (str(title), str(version), str(text or ""))
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


def cmd_judge(args: argparse.Namespace) -> None:
    qrels_doc = read(args.qrels)
    judged = read(args.judgements)
    if judged["golden_sha256"] != qrels_doc["golden_sha256"]:
        raise SystemExit("判定檔與 qrels 不是同一份題庫")
    if judged["relevant_from"] != qrels_doc["relevant_from"]:
        raise SystemExit("判定檔與 qrels 的相關門檻不同")
    tag = judged["sha256"][:12]
    try:
        qrels, graded_by, added = merge_judgements(qrels_doc, judged["rows"], tag)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    record = {
        "path": args.judgements.name,
        "sha256": judged["sha256"],
        "tag": tag,
        "judges": [j["name"] for j in judged["judges"]],
        "date": judged["date"],
        "prompt_sha256": judged["prompt"]["sha256"],
        "subset": judged["subset"],
        "added": added,
    }
    doc = write(
        args.out,
        {
            **qrels_doc,
            "qrels": qrels,
            "graded_by": graded_by,
            "judgements": [*qrels_doc["judgements"], record],
        },
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
    j = sub.add_parser("judge", help="把封存的模型判定檔併入 qrels")
    j.add_argument("--qrels", type=Path, required=True)
    j.add_argument("--judgements", type=Path, required=True)
    j.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    {"split": cmd_split, "judge": cmd_judge}[args.command](args)


if __name__ == "__main__":
    main()
