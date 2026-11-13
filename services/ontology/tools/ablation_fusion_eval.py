"""Ablation of the reciprocal-rank-fusion constant k and the per-arm candidate pool P.

    uv run python tools/ablation_fusion_eval.py arms <golden.yaml> --qrels eval/golden-qrels.json \\
        --split eval/golden-split.json --subset dev --out <dir>/arms-dev-r1.json
    uv run python tools/ablation_fusion_eval.py grid --qrels eval/golden-qrels.json \\
        --arms <dir>/arms-dev-r1.json --arms <dir>/arms-dev-r2.json --arms <dir>/arms-dev-r3.json \\
        [--export-runs <dir>/runs] --out eval/ablation-fusion-dev-<date>.json

`arms` asks the database for each question's two candidate lists, once per pool size, through the
service's own `retrieval.search_hybrid`: the lexical arm with no embedding, the semantic arm with an
empty term list (an empty tsquery matches nothing), and the fused list as the service returns it.
Law-name scoping is the service's (`law_scope`, with the fall-back to the whole corpus when the
scope has no hit). Nothing is written to the database.

`grid` fuses the two lists offline for every (P, k) with exactly the service's rule — a reciprocal
rank sum over the first P of each arm, ties broken by the article's current version id — and scores
every cell with ranx against the qrels (R@1/5/10, MRR, nDCG@10, Hole@10). Before anything else it
checks that the offline rule at the service's own k (60) returns the very list the service returned
for every question; a mismatch stops the run. Each cell is compared with the baseline cell by a
paired Fisher randomisation test, Holm-corrected over the cells, in every round. A cell is reported
as different only when all rounds agree in direction with a corrected p < 0.05 and the mean gap is
larger than the spread between the rounds of either cell; fewer than three rounds claim nothing.

The cell runs can be exported (`--export-runs`) in the run-file shape `relevance_judge.py pool`
reads, so the holes of every cell are judged before the final scoring.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_retrieval_eval as gre
import golden_set as gs

from envcite_ontology.embedding import to_sql
from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load
from envcite_ontology.store import LANGUAGE, SEARCH_POOL, OntologyStore
from envcite_ontology.verification import config
from envcite_segmenter import Segmenter

POOLS = (30, 50, 100)
KS = (20, 60, 100)
#: 服務現行的組合：候選池 `SEARCH_POOL`，名次倒數的常數寫死在 `search_hybrid` 的 60。
BASELINE = (SEARCH_POOL, 60)
SERVICE_K = 60
METRICS = ("hit_rate@1", "hit_rate@5", "hit_rate@10", "mrr", "ndcg@10")
#: 檢定與判讀的主指標；其餘只描述。
PRIMARY = ("ndcg@10", "mrr")
ALPHA = 0.05
MIN_ROUNDS = 3

#: 一個 (版本編號, 條文代號)：版本編號是融合同分時的次序（服務的 `av.id`）。
Hit = tuple[int, str]

_ARM = """
WITH h AS MATERIALIZED (
    SELECT * FROM retrieval.search_hybrid(
        %(terms)s, %(tokenizer)s, %(embedding)s::vector, %(model)s, %(on)s::date, %(language)s,
        %(limit)s, %(pool)s, %(resources)s::bigint[])
)
SELECT h.version_id, r.jurisdiction, r.local_id, corpus.article_number(h.path)
  FROM h JOIN corpus.legal_resource AS r ON r.id = h.resource_id
 ORDER BY h.rank_score DESC, h.version_id
"""


#: `search_hybrid` 的 `near` 臂（範圍為全庫時）逐字的近鄰查詢，只用來看計畫與回傳列數。
_NEAR = """
SELECT i.content_sha256, 1 - (i.embedding <=> %(embedding)s::vector) AS similarity
  FROM retrieval.passage AS i
 WHERE i.model = %(model)s
 ORDER BY i.embedding <=> %(embedding)s::vector
 LIMIT %(limit)s
"""


def near_probe(
    conn: psycopg.Connection[Any], embedding_sql: str, model: str, limit: int
) -> dict[str, Any]:
    """近鄰查詢的計畫節點與實際回傳列數：HNSW 一次最多回 `hnsw.ef_search` 列，LIMIT 再大也一樣。"""
    args = {"embedding": embedding_sql, "model": model, "limit": limit}
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + _NEAR, args).fetchone()
    assert plan is not None

    def nodes(node: Mapping[str, Any]) -> list[str]:
        found = [str(node["Node Type"])]
        for child in node.get("Plans", []):
            found += nodes(child)
        return found

    rows = conn.execute(_NEAR, args).fetchall()
    return {
        "limit": limit,
        "returned_rows": len(rows),
        "plan_nodes": sorted(set(nodes(plan[0][0]["Plan"]))),
    }


def rrf_fuse(lex: Sequence[Hit], sem: Sequence[Hit], *, k: int, pool: int) -> list[str]:
    """兩臂各取前 pool 條，名次倒數相加（精確分數）；同分依版本編號。回傳條文代號，高到低。"""
    score: dict[str, Fraction] = defaultdict(Fraction)
    version: dict[str, int] = {}
    for arm in (lex, sem):
        for rank, (vid, doc) in enumerate(arm[:pool], 1):
            score[doc] += Fraction(1, k + rank)
            version[doc] = vid
    return sorted(score, key=lambda d: (-score[d], version[d]))


def holm(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm 逐步校正：依 p 由小到大乘以剩餘檢定數，取累積最大值並以 1 封頂。"""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    out: dict[str, float] = {}
    running = 0.0
    for i, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - i) * p))
        out[name] = running
    return out


def arm_rows(
    conn: psycopg.Connection[Any],
    *,
    terms: str,
    tokenizer: str,
    on: str,
    embedding_sql: str | None,
    model: str,
    pool: int,
    resources: Sequence[int] | None,
) -> list[Hit]:
    """`search_hybrid` 在候選池 pool、取回 pool 條時的輸出，已轉成條文代號。"""
    rows = conn.execute(
        _ARM,
        {
            "terms": terms,
            "tokenizer": tokenizer,
            "embedding": embedding_sql,
            "model": model,
            "on": on,
            "language": LANGUAGE,
            "limit": pool,
            "pool": pool,
            "resources": None if resources is None else list(resources),
        },
    ).fetchall()
    return [(int(vid), gs.doc_id(lid, number, jur)) for vid, jur, lid, number in rows]


def collect(
    conn: psycopg.Connection[Any],
    *,
    terms: str,
    tokenizer: str,
    on: str,
    vector: Sequence[float],
    model: str,
    pools: Sequence[int],
    laws: Sequence[str],
) -> dict[int, dict[str, list[Hit]]]:
    """一題在每個候選池下的字面臂、語意臂與服務融合後的清單。"""
    resources: list[int] | None = None
    if laws:
        row = conn.execute(
            "SELECT retrieval.law_scope(%s::text[], %s, %s::date)", (list(laws), LANGUAGE, on)
        ).fetchone()
        assert row is not None
        resources = row[0]
    embedding_sql = to_sql(vector)
    common: dict[str, Any] = {"tokenizer": tokenizer, "on": on, "model": model}
    out: dict[int, dict[str, list[Hit]]] = {}
    # 字面臂的候選池是前 pool 名（ORDER BY … LIMIT pool），小的池是大的池的前綴：只查最大的一次。
    # 與服務是否一致由 `fidelity` 逐題核對。
    lexical = arm_rows(
        conn,
        terms=terms,
        embedding_sql=None,
        pool=max(pools),
        resources=resources,
        **common,
    )
    for pool in pools:
        scope = resources
        fused = arm_rows(
            conn, terms=terms, embedding_sql=embedding_sql, pool=pool, resources=scope, **common
        )
        if laws and not fused:  # 服務的退路：點名的法規範圍內沒有任何命中，改找全庫
            scope = None
            fused = arm_rows(
                conn, terms=terms, embedding_sql=embedding_sql, pool=pool, resources=None, **common
            )
        out[pool] = {
            "hybrid": fused,
            "lexical": (
                lexical[:pool]
                if scope == resources
                else arm_rows(
                    conn, terms=terms, embedding_sql=None, pool=pool, resources=None, **common
                )
            ),
            "semantic": arm_rows(
                conn, terms="", embedding_sql=embedding_sql, pool=pool, resources=scope, **common
            ),
        }
    return out


def service_relation(service: Sequence[str], fused: Sequence[str]) -> str:
    """服務對外的清單（`store.search`）與同一候選池的融合清單的關係。

    服務在融合清單之後做罰則帶出（`search_expanded`）：處罰某一條的罰則條緊接在它之後插入；
    若那條本來也在融合清單的後段，就被拉到前面。清單長度上限再把尾端（含被拉走之處以後的條）截掉。
    去掉服務清單中不在融合清單裡的條（帶出的罰則條）之後：
    - `identical`：與融合清單的前綴完全相同，罰則條只是插入；
    - `reordered`：次序不同，罰則帶出把融合清單後段的條拉到前面；
    - `different`：服務的清單沒有任何融合清單的條，離線融合說明不了它。
    """
    rank = {d: i for i, d in enumerate(fused)}
    seen = [d for d in service if d in rank]
    if not seen:
        return "different" if fused else "identical"
    return "identical" if seen == list(fused[: len(seen)]) else "reordered"


def cmd_arms(args: argparse.Namespace) -> None:
    items, golden_sha = gre._golden(args.golden)
    qrels_doc, split_doc = gs.read(args.qrels), gs.read(args.split)
    gs.require_golden(qrels_doc, golden_sha, str(args.qrels))
    gs.require_golden(split_doc, golden_sha, str(args.split))
    rows = [
        r
        for r in gre.questions(items, qrels_doc, gs.subset_ids(split_doc, args.subset))
        if r.status == "scored"
    ]
    db = load(DatabaseSettings)
    embedder = config.embedder_from_settings(load(ModelsSettings), timeout=30.0)
    if embedder is None:
        raise SystemExit("沒有設定嵌入端點（ENVCITE_ONTOLOGY_MODELS 的 [embedding]）")
    on = date.today().isoformat()
    pools = tuple(args.pools)
    with psycopg.connect(db.dsn) as c:
        snaps = gre.snapshots(c)
        c.execute("SELECT '[1]'::vector")  # 載入 pgvector，hnsw.* 設定才存在
        ef_row = c.execute("SELECT current_setting('hnsw.ef_search')").fetchone()
        assert ef_row is not None
        ef_default = ef_row[0]
        gre.require_snapshots(qrels_doc, snaps)
        counts = gre.corpus_counts(c)
    runs: dict[str, dict[str, dict[str, list[list[Any]]]]] = {
        str(p): {"hybrid": {}, "lexical": {}, "semantic": {}} for p in pools
    }
    relations: dict[str, list[str]] = {"identical": [], "reordered": [], "different": []}
    probe: dict[str, Any] = {}
    with OntologyStore.from_dsn(db.dsn) as store, psycopg.connect(db.dsn) as conn:
        conn.read_only = True
        if args.ef_search is not None:
            conn.execute("SELECT '[1]'::vector")
            conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(args.ef_search),))
        segmenter = Segmenter(store.lexicon())
        law_names = store.law_names()
        for done, r in enumerate(rows, 1):
            if done % 20 == 0:
                print(f"{done}/{len(rows)} 題", file=sys.stderr, flush=True)
            terms = segmenter.to_tsvector_input(r.question)
            vector = embedder.embed([r.question])[0]
            laws = law_names.find(r.question)
            if not probe:
                probe = near_probe(conn, to_sql(vector), embedder.model, 4 * max(pools))
            got = collect(
                conn,
                terms=terms,
                tokenizer=segmenter.tokenizer_id,
                on=on,
                vector=vector,
                model=embedder.model,
                pools=pools,
                laws=laws,
            )
            if args.ef_search is None and SEARCH_POOL in pools:
                service = [
                    gs.doc_id(h.local_id, h.number, h.jurisdiction)
                    for h in store.search(
                        terms,
                        tokenizer=segmenter.tokenizer_id,
                        as_of=on,
                        limit=SEARCH_POOL,
                        embedding=vector,
                        model=embedder.model,
                        laws=laws,
                    )
                ]
                relation = service_relation(service, [d for _, d in got[SEARCH_POOL]["hybrid"]])
                relations[relation].append(r.id)
            for pool, lists in got.items():
                for name, hits in lists.items():
                    runs[str(pool)][name][r.id] = [[vid, doc] for vid, doc in hits]
        tokenizer = segmenter.tokenizer_id
    doc = gs.write(
        args.out,
        {
            "kind": "fusion-arms",
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "as_of": on,
            "envcite_commit": gre.git("rev-parse", "HEAD"),
            "git_dirty": bool(gre.git("status", "--porcelain", "--", "..")),
            "golden_sha256": golden_sha,
            "split_sha256": split_doc["sha256"],
            "subset": args.subset,
            "snapshots": snaps,
            "corpus": counts,
            "retrieval": {
                "tokenizer": tokenizer,
                "embedding_model": embedder.model,
                "law_names": True,
                "pools": list(pools),
                #: 近鄰搜尋一次最多回的段落數（pgvector 的 hnsw.ef_search）；None 為伺服器的設定。
                "hnsw_ef_search": args.ef_search if args.ef_search is not None else int(ef_default),
                "hnsw_ef_search_is_service_setting": args.ef_search is None,
                "near_probe": probe,
            },
            #: 候選池 = 服務現行值時，服務對外的 `store.search` 與這裡的融合清單逐題比對關係
            #: （`service_relation`）；改了 ef_search 的收集不走服務的連線，不比對。
            "service_path_check": (
                {
                    "questions": len(rows),
                    **{k: len(v) for k, v in relations.items()},
                    "different_questions": sorted(relations["different"]),
                }
                if args.ef_search is None and SEARCH_POOL in pools
                else None
            ),
            "pools": runs,
        },
    )
    print(f"{len(rows)} 題、候選池 {list(pools)} 已寫入 {args.out}（sha256 {doc['sha256']}）")


def _hits(doc: Mapping[str, Any], pool: int, name: str) -> dict[str, list[Hit]]:
    return {
        q: [(int(vid), str(d)) for vid, d in hits]
        for q, hits in doc["pools"][str(pool)][name].items()
    }


def fidelity(doc: Mapping[str, Any]) -> dict[str, Any]:
    """服務現行的 k（60）下，離線融合是否逐題給出服務自己回傳的清單。"""
    out: dict[str, Any] = {}
    for pool in doc["retrieval"]["pools"]:
        lex, sem, hyb = (_hits(doc, pool, n) for n in ("lexical", "semantic", "hybrid"))
        different = [
            q
            for q in hyb
            if rrf_fuse(lex[q], sem[q], k=SERVICE_K, pool=pool)[:pool] != [d for _, d in hyb[q]]
        ]
        out[str(pool)] = {"questions": len(hyb), "identical": len(hyb) - len(different)}
        if different:
            out[str(pool)]["different_questions"] = sorted(different)[:20]
    return out


def semantic_depth(doc: Mapping[str, Any]) -> dict[str, Any]:
    """語意臂每題實際回傳幾條：近鄰搜尋給得比候選池少時，加大候選池不增加語意臂的候選。"""
    out: dict[str, Any] = {}
    for pool in doc["retrieval"]["pools"]:
        sizes = sorted(len(v) for v in _hits(doc, pool, "semantic").values())
        n = len(sizes)
        out[str(pool)] = {
            "questions": n,
            "min": sizes[0] if sizes else None,
            "median": sizes[n // 2] if sizes else None,
            "max": sizes[-1] if sizes else None,
            "full_pool": sum(s >= pool for s in sizes),
        }
    return out


def cell_rankings(
    doc: Mapping[str, Any], *, pools: Sequence[int], ks: Sequence[int]
) -> dict[str, gs.Ranking]:
    """每個 (P, k) 格的融合清單，加上字面臂與語意臂單獨的對照。"""
    out: dict[str, gs.Ranking] = {}
    for pool in pools:
        lex, sem = _hits(doc, pool, "lexical"), _hits(doc, pool, "semantic")
        for k in ks:
            out[f"P{pool}-k{k}"] = {q: rrf_fuse(lex[q], sem[q], k=k, pool=pool) for q in lex}
    base = BASELINE[0]
    if base in pools:
        out["lexical-only"] = {
            q: [d for _, d in hits] for q, hits in _hits(doc, base, "lexical").items()
        }
        out["semantic-only"] = {
            q: [d for _, d in hits] for q, hits in _hits(doc, base, "semantic").items()
        }
    return out


def score(qrels: gs.Qrels, ranking: gs.Ranking, relevant_from: int) -> dict[str, float | None]:
    judged = {q: qrels[q] for q in ranking}
    got = gs.evaluate(judged, ranking, METRICS, relevant_from=relevant_from)
    hole = gs.hole_rate(judged, ranking)
    return {
        **{m: round(v, 4) for m, v in got.items()},
        "hole@10": None if hole is None else round(hole, 4),
    }


def by_bucket(
    qrels: gs.Qrels,
    ranking: gs.Ranking,
    buckets: Mapping[str, str],
    relevant_from: int,
) -> dict[str, dict[str, Any]]:
    """桶內的題數與主要指標，描述用（不做檢定）。"""
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(set(buckets.get(q, "") for q in ranking)):
        part = {q: r for q, r in ranking.items() if buckets.get(q, "") == name}
        got = score(qrels, part, relevant_from)
        out[name] = {"n": len(part), **{m: got[m] for m in ("hit_rate@10", "mrr", "ndcg@10")}}
    return out


def contrasts(
    qrels: gs.Qrels,
    relevant_from: int,
    pairs: Mapping[str, tuple[Sequence[gs.Ranking], Sequence[gs.Ranking]]],
) -> dict[str, dict[str, Any]]:
    """每個標籤：左系統（各輪的清單）減右系統，逐輪做成對 Fisher 隨機化檢定。

    Holm 校正在同一輪、同一指標的所有標籤上。判有差（`different`）要同時：至少 `MIN_ROUNDS` 輪、
    各輪方向一致、每輪校正後 p < `ALPHA`、平均差大於兩邊各自的輪間範圍。
    """
    out: dict[str, dict[str, Any]] = {label: {} for label in pairs}
    if not pairs:
        return out
    n_rounds = len(next(iter(pairs.values()))[0])
    for metric in PRIMARY:
        adjusted_by_round: list[dict[str, float]] = []
        raw_by_round: list[dict[str, float]] = []
        for i in range(n_rounds):
            raw = {}
            for label, (left, right) in pairs.items():
                res = gs.compare(
                    qrels,
                    {"left": left[i], "right": right[i]},
                    [metric],
                    relevant_from=relevant_from,
                )
                raw[label] = float(res["left"]["comparisons"]["right"][metric])
            raw_by_round.append(raw)
            adjusted_by_round.append(holm(raw))
        for label, (left, right) in pairs.items():
            lefts = [float(score(qrels, r, relevant_from)[metric] or 0.0) for r in left]
            rights = [float(score(qrels, r, relevant_from)[metric] or 0.0) for r in right]
            mean_left, mean_right = sum(lefts) / len(lefts), sum(rights) / len(rights)
            round_gaps = [a - b for a, b in zip(lefts, rights, strict=True)]
            noise = max(max(lefts) - min(lefts), max(rights) - min(rights))
            same_direction = all(g > 0 for g in round_gaps) or all(g < 0 for g in round_gaps)
            adjusted = [a[label] for a in adjusted_by_round]
            gap = mean_left - mean_right
            out[label][metric] = {
                "mean_gap": round(gap, 4),
                "relative_gap": round(gap / mean_right, 4) if mean_right else None,
                "round_gaps": [round(g, 4) for g in round_gaps],
                "between_round_range": round(noise, 4),
                "p_raw_by_round": [round(r[label], 5) for r in raw_by_round],
                "p_holm_by_round": [round(p, 4) for p in adjusted],
                "different": (
                    n_rounds >= MIN_ROUNDS
                    and same_direction
                    and all(p < ALPHA for p in adjusted)
                    and abs(gap) > noise
                ),
            }
    return out


def _load_arms(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """一組輪次的 arms：格式、各輪一致，且離線融合與服務逐題相同；回傳（文件, 核對結果）。"""
    docs = [gs.read(p) for p in paths]
    for d, p in zip(docs, paths, strict=True):
        if d.get("kind") != "fusion-arms":
            raise SystemExit(f"{p} 不是 arms 的輸出")
    for key in ("golden_sha256", "split_sha256", "subset", "snapshots", "retrieval"):
        if len({json.dumps(d[key], sort_keys=True) for d in docs}) != 1:
            raise SystemExit(f"各輪 arms 的 {key} 不同")
    checks = [fidelity(d) for d in docs]
    for c, p in zip(checks, paths, strict=True):
        bad = {pool: v for pool, v in c.items() if v["identical"] != v["questions"]}
        if bad:
            raise SystemExit(f"離線融合與服務的 search_hybrid 不一致（{p}）：{bad}")
    return docs, checks


def cmd_grid(args: argparse.Namespace) -> None:
    qrels_doc = gs.read(args.qrels)
    qrels: gs.Qrels = qrels_doc["qrels"]
    relevant_from = int(qrels_doc["relevant_from"])
    docs, checks = _load_arms(args.arms)
    pools = tuple(sorted({p for d in docs for p in d["retrieval"]["pools"]}))
    if BASELINE[0] not in pools:
        raise SystemExit(f"arms 要含基準的候選池 {BASELINE[0]}")

    rounds = [cell_rankings(d, pools=pools, ks=args.ks) for d in docs]
    base = f"P{BASELINE[0]}-k{BASELINE[1]}"
    if args.only:
        unknown = [n for n in args.only if n not in rounds[0]]
        if unknown:
            raise SystemExit(f"--only 的格子不存在：{unknown}")
        rounds = [{n: r[n] for n in args.only} for r in rounds]
    names = list(rounds[0])
    if base not in names:
        raise SystemExit(f"格子要含基準 {base}")
    if args.export_runs:
        args.export_runs.mkdir(parents=True, exist_ok=True)
        for i, cells in enumerate(rounds, 1):
            for name, ranking in cells.items():
                gs.write(
                    args.export_runs / f"{name}.r{i}.run.json",
                    {
                        "arm": name,
                        "round": i,
                        "golden_sha256": docs[i - 1]["golden_sha256"],
                        "split_sha256": docs[i - 1]["split_sha256"],
                        "subset": docs[i - 1]["subset"],
                        "snapshots": docs[i - 1]["snapshots"],
                        "ranking": ranking,
                    },
                )

    per_round = [{n: score(qrels, r[n], relevant_from) for n in names} for r in rounds]
    cells: dict[str, Any] = {}
    for n in names:
        by_metric = {m: [pr[n][m] for pr in per_round] for m in (*METRICS, "hole@10")}
        cells[n] = {
            "rounds": by_metric,
            "mean": {m: round(sum(v) / len(v), 4) for m, v in by_metric.items() if None not in v},
            "range": {m: round(max(v) - min(v), 4) for m, v in by_metric.items() if None not in v},
        }

    buckets: dict[str, str] | None = None
    if args.golden:
        items, _ = gre._golden(args.golden)
        buckets = {str(i["id"]): str(i["bucket"]) for i in items}
    cell_buckets = (
        {n: by_bucket(qrels, rounds[0][n], buckets, relevant_from) for n in names}
        if buckets
        else None
    )
    versus_baseline = contrasts(
        qrels,
        relevant_from,
        {n: ([r[n] for r in rounds], [r[base] for r in rounds]) for n in names if n != base},
    )
    versus_semantic = (
        contrasts(
            qrels,
            relevant_from,
            {
                n: ([r[n] for r in rounds], [r["semantic-only"] for r in rounds])
                for n in names
                if n.startswith("P")
            },
        )
        if "semantic-only" in names
        else None
    )
    versus_other = None
    other: dict[str, Any] | None = None
    if args.other_arms:
        docs_o, _ = _load_arms(args.other_arms)
        if len(docs_o) != len(docs):
            raise SystemExit("兩個條件的輪數要相同")
        for key in ("golden_sha256", "split_sha256", "subset", "snapshots"):
            if json.dumps(docs_o[0][key], sort_keys=True) != json.dumps(
                docs[0][key], sort_keys=True
            ):
                raise SystemExit(f"兩個條件的 {key} 不同")
        rounds_o = [
            {n: r[n] for n in names}
            for r in (cell_rankings(d, pools=pools, ks=args.ks) for d in docs_o)
        ]
        versus_other = contrasts(
            qrels,
            relevant_from,
            {n: ([r[n] for r in rounds_o], [r[n] for r in rounds]) for n in names},
        )
        other = {
            "arms": [
                {"path": p.name, "sha256": d["sha256"], "run_at": d["run_at"]}
                for p, d in zip(args.other_arms, docs_o, strict=True)
            ],
            "retrieval": docs_o[0]["retrieval"],
        }

    report = {
        "kind": "fusion-ablation",
        "subset": docs[0]["subset"],
        "rounds": len(docs),
        "inputs": {
            "arms": [
                {"path": p.name, "sha256": d["sha256"], "run_at": d["run_at"]}
                for p, d in zip(args.arms, docs, strict=True)
            ],
            "qrels": {"path": args.qrels.name, "sha256": qrels_doc["sha256"]},
            "golden_sha256": docs[0]["golden_sha256"],
            "split_sha256": docs[0]["split_sha256"],
            "envcite_commit": docs[0]["envcite_commit"],
            "snapshots": docs[0]["snapshots"],
            "retrieval": docs[0]["retrieval"],
        },
        "baseline": base,
        "questions": len(next(iter(rounds[0].values()))),
        "relevant_from": relevant_from,
        "fidelity_to_service": checks,
        "service_path_check": [d["service_path_check"] for d in docs],
        "semantic_arm_depth": [semantic_depth(d) for d in docs],
        "cells": cells,
        "by_bucket_round1": cell_buckets,
        "versus_baseline": versus_baseline,
        "versus_semantic_only": versus_semantic,
        "other_condition": other,
        "versus_other_condition": versus_other,
        "claim_rule": (
            f"各輪方向一致、Holm 校正後 p < {ALPHA}、平均差大於兩格的輪間範圍，"
            f"且至少 {MIN_ROUNDS} 輪"
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"消融報告已寫入 {args.out}")
    else:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="RRF 融合常數與候選池深度消融")
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("arms", help="向資料庫收集兩臂候選與服務融合後的清單")
    a.add_argument("golden", type=Path)
    a.add_argument("--qrels", type=Path, required=True)
    a.add_argument("--split", type=Path, required=True)
    a.add_argument("--subset", choices=("dev", "test"), required=True)
    a.add_argument("--pools", type=int, nargs="+", default=list(POOLS))
    a.add_argument(
        "--ef-search",
        type=int,
        help="近鄰搜尋的 hnsw.ef_search；省略為資料庫現行設定（服務所用）。"
        "設得比候選池大，語意臂才取得到候選池那麼多條",
    )
    a.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("grid", help="離線融合各 (P, k)，算指標並與基準檢定")
    g.add_argument("--qrels", type=Path, required=True)
    g.add_argument("--arms", type=Path, action="append", required=True, help="每輪一份")
    g.add_argument(
        "--other-arms",
        type=Path,
        action="append",
        help="另一個條件（如另一個 ef_search）的各輪 arms；逐格與本條件檢定（另一個減本條件）",
    )
    g.add_argument(
        "--only",
        nargs="+",
        help="只做這幾格（含基準）：沒有判完洞的格子不該拿來比；測試集只報事先指定的幾格",
    )
    g.add_argument("--golden", type=Path, help="題庫：給了就另列各桶的指標（描述用）")
    g.add_argument("--ks", type=int, nargs="+", default=list(KS))
    g.add_argument("--export-runs", type=Path, help="各格的 run 檔目錄（供 relevance_judge pool）")
    g.add_argument("--out", type=Path)
    args = parser.parse_args()
    {"arms": cmd_arms, "grid": cmd_grid}[args.command](args)


if __name__ == "__main__":
    main()
