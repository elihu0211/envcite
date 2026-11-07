"""Retrieval against the golden question set (read-only): qrels, runs, metrics and tests.

    uv run python tools/golden_retrieval_eval.py qrels <golden.yaml> --out eval/golden-qrels.json
    uv run python tools/golden_retrieval_eval.py measure <golden.yaml> \\
        --qrels eval/golden-qrels.json --split eval/golden-split.json --subset dev \\
        --out eval/golden-retrieval-<date>
    uv run python tools/golden_retrieval_eval.py compare --qrels eval/golden-qrels.json \\
        <a>.hybrid.run.json <b>.hybrid.run.json [--out eval/golden-compare-<date>]

`qrels` maps each answerable question's expected articles (`citation_allowlist`: law name, article
number, sometimes a `<pcode>-art-<n>` id) to this corpus — law name to pcode by exact
`legal_expression.title` (an id's pcode wins), article number to `corpus.provision.article_path` —
and writes them as qrels keyed by article id (relevance `gs.UPSTREAM_GRADE`, listed as `upstream`).
A question none of whose articles map
is `unmapped`: listed with the reason, never in the qrels, never scored as a miss. Negatives are
not in the qrels either; they measure refusal. The qrels records the golden set's sha256 and the
corpus snapshots it was mapped against; a measurement on other snapshots stops (map again).

`measure` searches the questions of one split subset with the same `OntologyStore.search` the
service uses, writes one run per arm (`<out>.<arm>.run.json`) and a report scored from qrels and
run by ranx: R@k is ranx `hit_rate@k` (any article of relevance ≥ `relevant_from` within the
top k), MRR is ranx `mrr`, nDCG@10 is ranx `ndcg@10` on the graded relevance. Hole@10 is the share
of the top 10 that the qrels has no judgement for; those are the articles `relevance_judge.py pool`
judges. Negatives only record each arm's top score, for the floor tools. Tune on `dev`; `test` is
reported once, after the choice is made.

`compare` scores two or more runs of the same subset against one qrels (possibly newer than the
runs) and tests every pair with Fisher's randomization test. A difference that does not pass is not
an improvement or a regression.

Every report records the commit, the corpus snapshots and the sha256 of the golden set, the qrels
and the split. Database and embedder come from the environment (ENVCITE_ONTOLOGY_DSN,
ENVCITE_ONTOLOGY_MODELS); with no embedding declared only the lexical arm runs and the result
says so.

`--scope <golden_scope.py output>` limits the search to that set of laws without touching the
retrieval function: the service's `search` is asked for every candidate it can return and the hits
of laws outside the scope are dropped before the first k are taken. Each arm's candidate pool is
chosen before the filter, so a lexical pool crowded by out-of-scope laws yields fewer than k
in-scope hits; the number dropped per question is recorded. The embedding completion of the
scope is part of the meta.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs
from retrieval_eval import Embeds
from scoped_search import resource_ids, scoped_search

from envcite_ontology import embedding
from envcite_ontology.law_names import LawNames
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load
from envcite_ontology.store import SEARCH_POOL, OntologyStore
from envcite_segmenter import Segmenter

KS = (1, 5, 10, 20, 50)
METRICS = (*(f"hit_rate@{k}" for k in KS), "mrr", "ndcg@10")
BUCKETS = ("exact_id", "paraphrase", "multi_hop")
NEGATIVE = gs.NEGATIVE

LABELS = {**{f"hit_rate@{k}": f"R@{k}" for k in KS}, "mrr": "MRR", "ndcg@10": "nDCG@10"}

_ARTICLE = re.compile(r"^第\s*(\d+)(?:\s*條之\s*(\d+)|\s*-\s*(\d+)\s*條|\s*條)")
_ID = re.compile(
    r"^(?:(?P<jurisdiction>[A-Z]{2}-[A-Za-z0-9]+)/)?(?P<pcode>[A-Za-z0-9_.-]+)-art-(?P<number>\d+(?:-\d+)?)$"
)


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
    jurisdiction: str = "TW-ROC"


@dataclass(frozen=True)
class Result:
    id: str
    bucket: str
    question: str
    expected: list[dict[str, str | None]]
    mapped: list[dict[str, str]] = field(default_factory=list)
    unmapped_reasons: list[str] = field(default_factory=list)
    status: str = "scored"  # scored | unmapped | negative


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
    exp: Expected,
    by_title: dict[str, set[str]],
    provisions: dict[Any, Provision],
) -> tuple[Provision | None, str]:
    """（對應的條，沒有時的原因）。有 article_id 時其 pcode 與條號為準。"""
    jurisdiction = "TW-ROC"
    if exp.article_id:
        m = _ID.match(exp.article_id)
        if not m:
            return None, f"article_id 格式不合：{exp.article_id}"
        jurisdiction = m["jurisdiction"] or "TW-ROC"
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
    found = provisions.get((jurisdiction, pcode, number)) or provisions.get((pcode, number))
    if found is None:
        target = f"{jurisdiction}/{pcode}" if jurisdiction != "TW-ROC" else pcode
        return None, f"資料庫沒有這一條：{target} 第{number}條"
    return found, ""


def load_corpus(
    conn: psycopg.Connection[Any],
) -> tuple[dict[str, set[str]], dict[Any, Provision]]:
    """標題 → pcode 集合（取各法規所有版本名稱）；（pcode, 條號）及三元組 → 條。"""
    by_title: dict[str, set[str]] = defaultdict(set)
    for pcode, title in conn.execute(
        "SELECT r.local_id, e.title FROM corpus.legal_resource AS r"
        " JOIN corpus.legal_expression AS e ON e.resource_id = r.id"
    ):
        by_title[str(title)].add(str(pcode))
    titles: dict[tuple[str, str], str] = {
        (str(jur), str(pcode)): str(title)
        for jur, pcode, title in conn.execute(
            "SELECT DISTINCT ON (r.id) r.jurisdiction, r.local_id, e.title"
            "  FROM corpus.legal_resource AS r"
            "  JOIN corpus.legal_expression AS e ON e.resource_id = r.id"
            " ORDER BY r.id, e.version_date DESC"
        )
    }
    provisions: dict[Any, Provision] = {}
    for jur, pcode, number, path in conn.execute(
        "SELECT r.jurisdiction, r.local_id, corpus.article_number(p.article_path), p.article_path"
        "  FROM corpus.provision AS p JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        " WHERE p.provision_type = 'article' AND p.article_path IS NOT NULL"
    ):
        j_str, p_str, num_str = str(jur), str(pcode), str(number)
        prov = Provision(
            p_str, num_str, str(path), titles.get((j_str, p_str), ""), jurisdiction=j_str
        )
        provisions[(j_str, p_str, num_str)] = prov
        if j_str == "TW-ROC":
            provisions[(p_str, num_str)] = prov
    return dict(by_title), provisions


def resolve(
    items: Iterable[dict[str, Any]],
    by_title: dict[str, set[str]],
    provisions: dict[Any, Provision],
) -> list[Result]:
    results = []
    for item in items:
        exp = expected_of(item)
        mapped: list[dict[str, str]] = []
        reasons: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for e in exp:
            prov, why = map_expected(e, by_title, provisions)
            if prov is None:
                reasons.append(why)
            elif (prov.jurisdiction, prov.pcode, prov.number) not in seen:
                seen.add((prov.jurisdiction, prov.pcode, prov.number))
                mapped.append(
                    {
                        "jurisdiction": prov.jurisdiction,
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


def build_qrels(results: Iterable[Result]) -> tuple[gs.Qrels, dict[str, list[str]]]:
    """（qrels, 有對不到的條的題 → 原因）。只有 scored 的題進 qrels，期望條文的分數是
    `gs.UPSTREAM_GRADE`。"""
    qrels: gs.Qrels = {}
    reasons: dict[str, list[str]] = {}
    for r in results:
        if r.status == "scored":
            qrels[r.id] = {
                gs.doc_id(
                    m["pcode"], m["number"], m.get("jurisdiction", "TW-ROC")
                ): gs.UPSTREAM_GRADE
                for m in r.mapped
            }
        if r.unmapped_reasons:
            reasons[r.id] = r.unmapped_reasons
    return qrels, reasons


@dataclass(frozen=True)
class Measured:
    id: str
    bucket: str
    question: str
    status: str  # scored | unmapped | negative
    partial: bool = False  # 有期望條文對不到語料
    ranking: list[str] = field(default_factory=list)
    rank: int | None = None  # 第一個相關條的名次
    top_lexical: float | None = None
    top_semantic: float | None = None
    out_of_scope_dropped: int | None = None


def questions(
    items: Iterable[Mapping[str, Any]], qrels_doc: Mapping[str, Any], subset: frozenset[str]
) -> list[Measured]:
    """子集內的題；是否計分由 qrels 決定（在 qrels 裡且有相關條）。"""
    qrels: gs.Qrels = qrels_doc["qrels"]
    floor = int(qrels_doc["relevant_from"])
    reasons: dict[str, list[str]] = qrels_doc["unmapped"]
    out = []
    for item in items:
        qid = str(item["id"])
        if qid not in subset:
            continue
        bucket = str(item["bucket"])
        if bucket == NEGATIVE:
            status = "negative"
        elif any(rel >= floor for rel in qrels.get(qid, {}).values()):
            status = "scored"
        else:
            status = "unmapped"
        out.append(
            Measured(
                qid,
                bucket,
                str(item["question"]),
                status,
                partial=status == "scored" and qid in reasons,
            )
        )
    return out


def publishable(r: Measured) -> dict[str, Any]:
    """寫進報告的一題：不存題目文字（題庫是外部的，以 id 回題庫查），只留文字的雜湊供核對；
    排序在 run 檔。"""
    row = asdict(r)
    row["question_sha256"] = hashlib.sha256(row.pop("question").encode("utf-8")).hexdigest()
    del row["ranking"]
    return row


def search_all(
    store: OntologyStore,
    segmenter: Segmenter,
    rows: Sequence[Measured],
    embedder: Embeds | None,
    *,
    qrels: gs.Qrels,
    relevant_from: int,
    k: int,
    on: str,
    scope: frozenset[str] | None = None,
    pool: tuple[psycopg.Connection[Any], list[int]] | None = None,
    law_names: LawNames | None = None,
) -> list[Measured]:
    """逐題檢索前 k 條，回傳填了排序、第一個相關條的名次與最高分的新列；negative 只記最高分。

    有 `scope`（pcode 集合）時向檢索要它能給的全部候選，丟掉範圍外法規的命中後再取前 k 條。
    有 `pool`（連線，範圍內的法規內部編號）時改走 `scoped_search`：兩臂的候選池在取候選之前就
    限定在範圍內，取前 k 條後不再過濾（`out_of_scope_dropped` 照記，應為 0）。
    """
    out = []
    for r in rows:
        if r.status == "unmapped":
            out.append(r)
            continue
        vector = embedder.embed([r.question])[0] if embedder else None
        terms = segmenter.to_tsvector_input(r.question)
        if pool is not None:
            hits = scoped_search(
                pool[0],
                terms,
                tokenizer=segmenter.tokenizer_id,
                as_of=on,
                limit=k,
                embedding=vector,
                model=embedder.model if embedder else "",
                scope=pool[1],
            )
        else:
            hits = store.search(
                terms,
                tokenizer=segmenter.tokenizer_id,
                as_of=on,
                limit=2 * SEARCH_POOL if scope is not None else k,
                embedding=vector,
                model=embedder.model if embedder else "",
                laws=law_names.find(r.question) if law_names else (),
            )
        dropped = None
        if scope is not None:
            kept = [h for h in hits if h.law_id in scope]
            dropped = len(hits) - len(kept)
            hits = kept[:k]
        if pool is not None:
            assert dropped == 0, "候選池應只含範圍內的法規"
        semantic = [h.semantic_similarity for h in hits if h.semantic_similarity is not None]
        ranking = (
            [
                gs.doc_id(
                    getattr(h, "local_id", getattr(h, "law_id", "")),
                    h.number,
                    getattr(h, "jurisdiction", "TW-ROC"),
                )
                for h in hits
            ]
            if r.status == "scored"
            else []
        )
        wanted = {d for d, rel in qrels.get(r.id, {}).items() if rel >= relevant_from}
        out.append(
            replace(
                r,
                ranking=ranking,
                rank=next((i for i, d in enumerate(ranking, 1) if d in wanted), None),
                top_lexical=max((h.lexical_score for h in hits), default=0.0),
                top_semantic=max(semantic, default=None),
                out_of_scope_dropped=dropped,
            )
        )
    return out


def ranking_of(rows: Iterable[Measured]) -> gs.Ranking:
    return {r.id: r.ranking for r in rows if r.status == "scored"}


def check_equivalence(
    store: OntologyStore,
    conn: psycopg.Connection[Any],
    segmenter: Segmenter,
    results: Sequence[Measured],
    embedder: Embeds | None,
    *,
    on: str,
    n: int = 40,
) -> dict[str, Any]:
    """不加範圍時，`scoped_search` 與 `OntologyStore.search` 對 n 題（等距抽樣）的輸出
    是否完全相同。"""
    rows = [r for r in results if r.status != "unmapped"]
    sample = rows[:: max(1, len(rows) // n)][:n]
    compared = identical = 0
    for r in sample:
        terms = segmenter.to_tsvector_input(r.question)
        vector = embedder.embed([r.question])[0] if embedder else None
        for emb in (None, vector) if vector is not None else (None,):
            args: dict[str, Any] = {
                "tokenizer": segmenter.tokenizer_id,
                "as_of": on,
                "limit": max(KS),
                "embedding": emb,
                "model": embedder.model if (embedder and emb is not None) else "",
            }
            compared += 1
            identical += store.search(terms, **args) == scoped_search(conn, terms, **args)
    return {"questions": len(sample), "comparisons": compared, "identical": identical}


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


def scores(qrels: gs.Qrels, ranking: gs.Ranking, relevant_from: int) -> dict[str, Any]:
    """一組題的 ranx 指標與 Hole@10。"""
    judged = {q: qrels[q] for q in ranking}
    got = gs.evaluate(judged, ranking, METRICS, relevant_from=relevant_from)
    return {
        "n": len(ranking),
        **{m: round(v, 4) for m, v in got.items()},
        "hole@10": None if (h := gs.hole_rate(judged, ranking)) is None else round(h, 4),
    }


def summarize(rows: Sequence[Measured], qrels: gs.Qrels, relevant_from: int) -> dict[str, Any]:
    def counts(rs: Sequence[Measured]) -> dict[str, int]:
        return {
            "questions": len(rs),
            "unmapped": sum(r.status == "unmapped" for r in rs),
            "partially_mapped": sum(r.partial for r in rs),
        }

    by_bucket = {}
    for b in BUCKETS:
        rs = [r for r in rows if r.bucket == b]
        by_bucket[b] = {**counts(rs), **scores(qrels, ranking_of(rs), relevant_from)}
    answerable = [r for r in rows if r.bucket in BUCKETS]
    neg = [r for r in rows if r.bucket == NEGATIVE]
    return {
        "by_bucket": by_bucket,
        "overall": {**counts(answerable), **scores(qrels, ranking_of(answerable), relevant_from)},
        "negative": {
            "n": len(neg),
            "top_lexical": _distribution([r.top_lexical for r in neg]),
            "top_semantic": _distribution([r.top_semantic for r in neg]),
        },
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def versions(meta: Mapping[str, Any]) -> list[str]:
    g, q, s = meta["golden"], meta["qrels"], meta["split"]
    return [
        f"- 執行：{meta['run_at']}（as_of {meta['as_of']}）；commit `{meta['envcite_commit']}`"
        + ("（工作樹有未提交變更）" if meta["git_dirty"] else ""),
        f"- 題庫：`{g['path']}`，sha256 `{g['sha256']}`，{g['items']} 題",
        f"- qrels：`{q['path']}`，sha256 `{q['sha256']}`；模型判定檔 {q['judgements']} 份",
        f"- 切分：`{s['path']}`，sha256 `{s['sha256']}`；本報告為 **{s['subset']}**，"
        f"{s['questions']} 題",
        "- 語料快照："
        + "；".join(f"{x['feed']} data_date {x['data_date']}" for x in meta["snapshots"]),
    ]


def markdown(meta: dict[str, Any], arm: str, s: dict[str, Any]) -> str:
    r, c = meta["retrieval"], meta["corpus"]
    lines = [
        f"## {arm} 臂",
        "",
        "| 桶 | 題數 | unmapped | 部分對應 | 計分 n | "
        + " | ".join(f"R@{k}" for k in KS)
        + " | MRR | nDCG@10 | Hole@10 |",
        "|---|---|---|---|---|" + "---|" * (len(KS) + 3),
    ]
    rows = [(b, s["by_bucket"][b]) for b in BUCKETS] + [("總計", s["overall"])]
    for name, x in rows:
        lines.append(
            f"| {name} | {x['questions']} | {x['unmapped']} | {x['partially_mapped']} | {x['n']} | "
            + " | ".join(f"{x[f'hit_rate@{k}']:.3f}" for k in KS)
            + f" | {x['mrr']:.3f} | {x['ndcg@10']:.3f} | {_pct(x['hole@10'])} |"
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
        *versions(meta),
        f"- 語料：{c['legal_resources']} 部法規、{c['provisions']} 個條文單元、"
        f"{c['passages']} 筆 passage（其中有嵌入 {c['passages_with_embedding']}）",
        f"- 檢索：{r['arms']}；分詞器 `{r['tokenizer']}`；k = {r['k']}；"
        + ("問句點名的法規縮小範圍" if r.get("law_names") else "不依點名的法規縮小範圍"),
        *(
            [
                f"- 範圍：{sc['counts']['laws']} 部法規（題庫所引 {sc['counts']['cited']}、"
                f"授權命令 "
                f"{sc['counts']['authorized']}、總則法 {sc['counts']['general']}），"
                f"{sc['passages']} 段落，清單 sha256 `{sc['pcodes_sha256']}`（`{sc['path']}`）",
                f"- 嵌入：模型 `{sc['embedding_model']}`，完成 {sc['embedded']}/{sc['passages']}"
                f"（{sc['embedded'] / sc['passages']:.1%}）；方法：{sc['method']}",
            ]
            if (sc := meta.get("scope"))
            else []
        ),
        "- 指標由 ranx 以 qrels 與 run 算出：R@k 是 `hit_rate@k`（任一分數 ≥ 相關門檻的條在前 k"
        " 條），MRR 是 `mrr`，nDCG@10 以 0–3 分級為增益；"
        "Hole@10 是前 10 條中 qrels 沒有判定的比例，洞裡的條未經判定，Hole@10 不為 0 時召回是下界。"
        "unmapped 不計入分母，原因逐題列在 qrels；部分對應的題只以對得到的條文計。"
        "僅檢索層，不含答對率。",
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


def require_snapshots(qrels_doc: Mapping[str, Any], snaps: Sequence[Mapping[str, Any]]) -> None:
    """qrels 是對著哪些快照對應的，量測就只能在那些快照上做。"""

    def key(xs: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
        return sorted((str(x["feed"]), str(x["sha256"])) for x in xs)

    if key(qrels_doc["snapshots"]) != key(snaps):
        raise SystemExit(
            "資料庫的語料快照與 qrels 對應時的不同：以 `qrels` 重新對應，再以 golden_set.py judge "
            "重新併入判定檔"
        )


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


def load_scope(path: Path) -> dict[str, Any]:
    scope: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    pcodes = sorted(e["pcode"] for e in scope["laws"])
    if hashlib.sha256("\n".join(pcodes).encode()).hexdigest() != scope["pcodes_sha256"]:
        raise SystemExit(f"{path}: pcodes_sha256 與清單不符")
    return scope


def scope_completion(conn: psycopg.Connection[Any], embedder: Embeds, scope: frozenset[str]) -> int:
    """範圍內不同段落文字中，已有這個模型嵌入的數量。"""
    row = conn.execute(
        "SELECT count(DISTINCT v.content_sha256) FROM corpus.provision_version AS v"
        "  JOIN corpus.legal_resource AS r ON r.id = v.resource_id"
        "  JOIN retrieval.passage AS i ON i.content_sha256 = v.content_sha256"
        " WHERE v.superseded_at IS NULL AND r.local_id = ANY(%s)"
        "   AND i.embedding IS NOT NULL AND i.model = %s",
        (sorted(scope), embedder.model),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _golden(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    items: list[dict[str, Any]] = yaml.safe_load(raw)["items"]
    return items, hashlib.sha256(raw).hexdigest()


def cmd_qrels(args: argparse.Namespace) -> None:
    items, golden_sha = _golden(args.golden)
    db = load(DatabaseSettings)
    with psycopg.connect(db.dsn) as conn:
        conn.read_only = True
        by_title, provisions = load_corpus(conn)
        snaps = snapshots(conn)
    resolved = resolve(items, by_title, provisions)
    qrels, reasons = build_qrels(resolved)
    doc = gs.write(
        args.out,
        {
            "golden_sha256": golden_sha,
            "snapshots": [{k: s[k] for k in ("feed", "data_date", "sha256")} for s in snaps],
            "rule": "期望條文對到語料：法規名稱以 legal_expression.title 完全相同對 pcode"
            "（有 article_id 時其 pcode 為準），條號對 corpus.provision.article_path；"
            "alternatives 攤平；題庫給定的條（upstream）分數為 "
            f"{gs.UPSTREAM_GRADE}，模型判定只能往上調；其餘分數來自 judgements 所列的判定檔",
            "scale": list(gs.SCALE),
            "relevant_from": gs.RELEVANT_FROM,
            "counts": {
                "items": len(items),
                "scored": len(qrels),
                "unmapped": sum(r.status == "unmapped" for r in resolved),
                "negative": sum(r.status == "negative" for r in resolved),
            },
            "qrels": qrels,
            "upstream": {q: sorted(ds) for q, ds in qrels.items()},
            "graded_by": {},
            "unmapped": reasons,
            "judgements": [],
        },
    )
    print(f"{doc['counts']}；{args.out}：sha256 {doc['sha256']}")


def cmd_measure(args: argparse.Namespace) -> None:
    if args.k < max(KS):
        raise SystemExit(f"--k 至少 {max(KS)}（R@{max(KS)} 需要）")
    if args.scope_in_pool and not args.scope:
        raise SystemExit("--scope-in-pool 需要 --scope")
    items, golden_sha = _golden(args.golden)
    qrels_doc, split_doc = gs.read(args.qrels), gs.read(args.split)
    gs.require_golden(qrels_doc, golden_sha, str(args.qrels))
    gs.require_golden(split_doc, golden_sha, str(args.split))
    qrels: gs.Qrels = qrels_doc["qrels"]
    floor = int(qrels_doc["relevant_from"])
    rows = questions(items, qrels_doc, gs.subset_ids(split_doc, args.subset))
    db = load(DatabaseSettings)
    embedder = embedding.from_settings(load(ModelsSettings), timeout=30.0)
    on = date.today().isoformat()
    scope_doc = load_scope(args.scope) if args.scope else None
    scope = frozenset(e["pcode"] for e in scope_doc["laws"]) if scope_doc else None
    with psycopg.connect(db.dsn) as conn:
        snaps = snapshots(conn)
        require_snapshots(qrels_doc, snaps)
        counts = corpus_counts(conn)
        scope_embedded = scope_completion(conn, embedder, scope) if scope and embedder else None
    equivalence = None
    with (
        OntologyStore.from_dsn(db.dsn) as store,
        psycopg.connect(db.dsn) as rconn,
    ):
        rconn.read_only = True
        segmenter = Segmenter(store.lexicon())
        law_names = None if args.no_law_names else store.law_names()
        pool = (rconn, resource_ids(rconn, sorted(scope))) if args.scope_in_pool and scope else None
        if pool:
            equivalence = check_equivalence(store, rconn, segmenter, rows, embedder, on=on)
            if equivalence["identical"] != equivalence["comparisons"]:
                raise SystemExit(f"scoped_search 與 OntologyStore.search 不等價：{equivalence}")
        arms = {
            name: search_all(
                store,
                segmenter,
                rows,
                e,
                qrels=qrels,
                relevant_from=floor,
                k=args.k,
                on=on,
                scope=scope,
                pool=pool,
                law_names=law_names,
            )
            for name, e in (("lexical", None), ("hybrid", embedder))
            if name == "lexical" or e is not None
        }
        tokenizer = segmenter.tokenizer_id

    meta: dict[str, Any] = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": on,
        "envcite_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "..")),
        "golden": {"path": args.golden.name, "sha256": golden_sha, "items": len(items)},
        "qrels": {
            "path": args.qrels.name,
            "sha256": qrels_doc["sha256"],
            "judgements": len(qrels_doc["judgements"]),
        },
        "split": {
            "path": args.split.name,
            "sha256": split_doc["sha256"],
            "subset": args.subset,
            "questions": len(rows),
        },
        "snapshots": snaps,
        "corpus": counts,
        "retrieval": {
            "arms": "字面 + 混合" if embedder else "僅字面（未設定嵌入端點）",
            "passages_with_embedding": counts["passages_with_embedding"],
            "tokenizer": tokenizer,
            "k": args.k,
            "embedding_model": embedder.model if embedder else None,
            "law_names": law_names is not None,
        },
    }
    if scope_doc and scope:
        meta["scope"] = {
            "path": str(args.scope),
            "counts": scope_doc["counts"],
            "passages": scope_doc["passages"],
            "pcodes_sha256": scope_doc["pcodes_sha256"],
            "rule": scope_doc["rule"],
            "embedding_model": embedder.model if embedder else None,
            "embedded": scope_embedded,
            "method": (
                "唯讀重現 search_hybrid（scoped_search.py）；兩臂候選池在取候選之前就限定在範圍內，"
                "取前 k 條後不再過濾"
                if args.scope_in_pool
                else "檢索函式不變；取 2×候選池的候選，丟掉範圍外法規的命中後取前 k 條"
                "（lexical 臂的候選池在過濾前以全庫決定）"
            ),
            "scope_in_pool": args.scope_in_pool,
            "equivalence_check": equivalence,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    runs = {
        name: gs.write(
            args.out.parent / f"{args.out.name}.{name}.run.json",
            {
                "arm": name,
                "golden_sha256": golden_sha,
                "split_sha256": split_doc["sha256"],
                "subset": args.subset,
                "envcite_commit": meta["envcite_commit"],
                "git_dirty": meta["git_dirty"],
                "snapshots": snaps,
                "retrieval": meta["retrieval"],
                "scope": meta.get("scope"),
                "ranking": ranking_of(rs),
            },
        )
        for name, rs in arms.items()
    }
    summaries = {name: summarize(rs, qrels, floor) for name, rs in arms.items()}
    doc = {
        "meta": meta,
        "arms": {
            name: {
                "summary": summaries[name],
                "run": {"path": f"{args.out.name}.{name}.run.json", "sha256": runs[name]["sha256"]},
                "results": [publishable(r) for r in rs],
            }
            for name, rs in arms.items()
        },
    }
    json_path = args.out.parent / (args.out.name + ".json")
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    parts = [markdown(meta, name, s) for name, s in summaries.items()]
    md = parts[0] + "".join(p[p.index("## ") :] for p in parts[1:])
    (args.out.parent / (args.out.name + ".md")).write_text(md, encoding="utf-8")
    print(md)
    print(f"結果：{json_path}")


def compared(
    qrels_doc: Mapping[str, Any], runs: Mapping[str, Mapping[str, Any]], alpha: float
) -> dict[str, Any]:
    """同一子集的 run 對同一份 qrels：各自的指標與 Hole@10，兩兩的 Fisher p 值與結論。"""
    first = next(iter(runs.values()))
    for name, r in runs.items():
        gs.require_golden(r, qrels_doc["golden_sha256"], name)
        if (r["split_sha256"], r["subset"]) != (first["split_sha256"], first["subset"]):
            raise SystemExit(f"{name} 與其他 run 不是同一個切分子集")
        if set(r["ranking"]) != set(first["ranking"]):
            raise SystemExit(f"{name} 與其他 run 的題目不同")
    qids = set(first["ranking"])
    qrels = {q: d for q, d in qrels_doc["qrels"].items() if q in qids}
    rankings = {name: r["ranking"] for name, r in runs.items()}
    report = gs.compare(qrels, rankings, METRICS, relevant_from=int(qrels_doc["relevant_from"]))
    names = list(runs)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for m in METRICS:
                p = float(report[a]["comparisons"][b][m])
                diff = report[b]["scores"][m] - report[a]["scores"][m]
                pairs.append(
                    {
                        "a": a,
                        "b": b,
                        "metric": m,
                        "difference": round(diff, 4),
                        "p": p,
                        "significant": p < alpha,
                    }
                )
    return {
        "qrels_sha256": qrels_doc["sha256"],
        "split_sha256": first["split_sha256"],
        "subset": first["subset"],
        "questions": len(qids),
        "alpha": alpha,
        "test": "Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）",
        "runs": {
            name: {
                "sha256": r["sha256"],
                "scores": {m: round(float(report[name]["scores"][m]), 4) for m in METRICS},
                "hole@10": gs.hole_rate(qrels, r["ranking"]),
            }
            for name, r in runs.items()
        },
        "pairs": pairs,
    }


def compare_markdown(c: Mapping[str, Any]) -> str:
    lines = [
        "# 黃金題庫檢索比較（envcite）",
        "",
        f"- qrels sha256 `{c['qrels_sha256']}`；切分 sha256 `{c['split_sha256']}`，"
        f"子集 **{c['subset']}**，{c['questions']} 題",
        f"- 檢定：{c['test']}；α = {c['alpha']}。未通過檢定的差距不寫成改善或退步；"
        "Hole@10 偏高時，未判定的條可能改變結論。",
        "",
        "| run | " + " | ".join(f"R@{k}" for k in KS) + " | MRR | nDCG@10 | Hole@10 |",
        "|---|" + "---|" * (len(KS) + 3),
    ]
    for name, r in c["runs"].items():
        lines.append(
            f"| {name} | "
            + " | ".join(f"{r['scores'][m]:.3f}" for m in METRICS)
            + f" | {_pct(r['hole@10'])} |"
        )
    lines += ["", "| a → b | 指標 | 差（b − a） | p | 結論 |", "|---|---|---|---|---|"]
    for p in c["pairs"]:
        label = LABELS[p["metric"]]
        verdict = "差異顯著" if p["significant"] else "未通過檢定"
        lines.append(
            f"| {p['a']} → {p['b']} | {label} | {p['difference']:+.4f} | {p['p']:.4f} | {verdict} |"
        )
    return "\n".join(lines) + "\n"


def cmd_compare(args: argparse.Namespace) -> None:
    if len(args.runs) < 2:
        raise SystemExit("至少兩個 run")
    runs = {p.name.removesuffix(".run.json"): gs.read(p) for p in args.runs}
    if len(runs) != len(args.runs):
        raise SystemExit("run 檔名重複")
    c = compared(gs.read(args.qrels), runs, args.alpha)
    md = compare_markdown(c)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        (args.out.parent / (args.out.name + ".json")).write_text(
            json.dumps(c, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        (args.out.parent / (args.out.name + ".md")).write_text(md, encoding="utf-8")
    print(md)


def main() -> None:
    parser = argparse.ArgumentParser(prog="golden_retrieval_eval")
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("qrels", help="題庫的可答題對到語料，寫成 qrels")
    q.add_argument("golden", type=Path)
    q.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("measure", help="檢索一個切分子集，寫 run 與報告")
    m.add_argument("golden", type=Path)
    m.add_argument("--qrels", type=Path, required=True)
    m.add_argument("--split", type=Path, required=True)
    m.add_argument("--subset", choices=("dev", "test"), required=True)
    m.add_argument("--out", type=Path, required=True, help="輸出檔前綴（寫 .json、.md 與各臂 run）")
    m.add_argument("--k", type=int, default=max(KS))
    m.add_argument("--scope", type=Path, help="golden_scope.py 的輸出；限定檢索範圍")
    m.add_argument(
        "--scope-in-pool",
        action="store_true",
        help="兩臂的候選池在取候選之前就限定在範圍內（唯讀重現 search_hybrid）",
    )
    m.add_argument(
        "--no-law-names",
        action="store_true",
        help="不依問句點名的法規縮小範圍（服務端預設會）；只用來對照",
    )
    c = sub.add_parser("compare", help="同一子集的 run 兩兩檢定")
    c.add_argument("--qrels", type=Path, required=True)
    c.add_argument("runs", type=Path, nargs="+")
    c.add_argument("--alpha", type=float, default=0.05)
    c.add_argument("--out", type=Path, help="輸出檔前綴（寫 .json 與 .md）；省略只印出")
    args = parser.parse_args()
    {"qrels": cmd_qrels, "measure": cmd_measure, "compare": cmd_compare}[args.command](args)


if __name__ == "__main__":
    main()
