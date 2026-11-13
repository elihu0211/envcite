"""查詢分類與路由：列表規則依查詢型別選用，離線比較各方案（唯讀）。

    uv run python tools/query_routing.py prepare --keyword eval/keyword-golden-<日期>.json \\
        --native eval/native-golden-<日期>.json --out queries.json
    # 在 staging 的 ontology pod 內以標準輸入執行；輸出一題一行（JSONL）
    python - collect queries.json --skip done.txt < tools/query_routing.py > hits.jsonl
    uv run python tools/query_routing.py evaluate queries.json hits.jsonl --splits dev

動機（`keyword-renamed-2026-11-13.md`）：列表規則只有一套，覆蓋率門檻對短關鍵字有效（擋掉片段命中），
對舊名、問句這類查詢詞多的查詢卻把正確的條文擋出列表。這支工具把「查詢是哪一型」與「該型用哪一套規則」
拆開量：

- `prepare` 把關鍵字題庫（用語題、無答案題、舊名題）與原生題庫（用語、問句、跨條引用、無答案）
  合成同一份題單，每題標註答案的層級（條文或法規）；題目全部由程式從語料產生，不含人工詞表。
- `collect` 對每題呼叫一次 `SearchArticles`（列表路徑：前 10 條，函釋入口開啟），把每條命中的
  字面分數、語意相似度、覆蓋率與函釋文件數存成 JSONL。可續跑：`--skip` 是已完成的題號。
  並行度不得超過服務的嵌入名額（2），超過時語意臂被無聲略過；一題的命中裡一條語意相似度都沒有，
  視為語意臂被略過，重試幾次，仍然沒有就在紀錄上標 `semantic_skipped`。
- `evaluate` 只讀這兩份檔、不呼叫任何服務：把候選的路由方案套在同一批命中上，分型別報告。
- `compare-rules` 兩條規則逐題配對；`live-check` 對線上的 `/v2/search` 逐題查詢，與離線預測比對：

      ENVCITE_API_KEY 不設時會提示輸入（不顯示）；每題計入該租戶的搜尋用量。
      uv run python tools/query_routing.py live-check eval/query-routing-queries-2026-11-13.json \\
          eval/query-routing-hits-2026-11-13.jsonl --meta eval/query-routing-meta-2026-11-13.json \\
          --split dev --host https://<主機> --sample 60

方案只能依開發集選；測試集在定案後報一次。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

#: 查詢型別：決定哪一套列表規則適用。由題目的產生規則決定（不是由被測的分類器）。
#: term 是短關鍵字；question 是整句問句；name 是法規名稱（含舊名）。
CLASS_OF = {
    "kw_definition": "term",
    "kw_absent": "term",
    "definition_term": "term",
    "absent_term": "term",
    "renamed": "name",
    "absent_name": "name",
    "definition_question": "question",
    "absent_question": "question",
    "duty_penalty": "question",
    "delegation": "question",
    "applies_mutatis_mutandis": "question",
}

#: 共用的相關性下界與覆蓋率門檻（與 `relevance-floors.json`、`LawSearchOptions` 一致）。
LEXICAL_FLOOR = 0.5
SEMANTIC_FLOOR = 0.71
MIN_TERM_COVERAGE = 0.5


#: 法規類型後綴（法、條例、辦法…）至少要在這麼多部不同的法規名稱裡出現，才算一個類型。
SUFFIX_MIN_TITLES = 15


def type_suffixes(titles: Sequence[str], minimum: int = SUFFIX_MIN_TITLES) -> list[str]:
    """由法規名稱統計出來的類型後綴，依被指派到的法規數由多到少。

    結尾 1 至 2 字在至少 `minimum` 部法規出現才算常見；每部法規只指派到它最長的常見後綴
    （「管理辦法」是辦法、不是法），指派到的法規數不足 `minimum` 的後綴不算類型。後綴不是人工列的，
    換一批法規名稱就換一批結果。
    """
    distinct = set(titles)
    count: defaultdict[str, int] = defaultdict(int)
    for title in distinct:
        for n in (1, 2):
            if len(title) > n:
                count[title[-n:]] += 1
    frequent = {s for s, c in count.items() if c >= minimum}
    assigned: defaultdict[str, int] = defaultdict(int)
    for title in distinct:
        for n in (2, 1):
            if title[-n:] in frequent and len(title) > n:
                assigned[title[-n:]] += 1
                break
    return sorted((s for s, c in assigned.items() if c >= minimum), key=lambda s: (-assigned[s], s))


def absent_names(
    rows: Sequence[Mapping[str, Any]], suffixes: Sequence[str]
) -> list[dict[str, Any]]:
    """沒有答案的法規名稱題：已核對全文都沒有的詞，接上一個類型後綴（像一部不存在的法規）。

    後綴取長度至少 2 的類型，依詞的雜湊輪流選，與題庫增減無關。切分沿用詞的切分。
    """
    import hashlib

    kinds = [s for s in suffixes if len(s) >= 2][:4]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row["kind"] not in ("kw_absent", "absent_term") or row["query"] in seen or not kinds:
            continue
        seen.add(row["query"])
        pick = kinds[hashlib.sha256(row["query"].encode()).digest()[0] % len(kinds)]
        out.append(
            _row(
                f"gen:absent_name:{row['query']}{pick}",
                "absent_name",
                row["split"],
                row["query"] + pick,
                "article",
                [],
            )
        )
    return out


def law_titles(keyword: dict[str, Any], native: dict[str, Any]) -> list[str]:
    """題庫答案涉及的法規名稱（現名）。"""
    titles = [e["law"] for item in keyword["items"] for e in item["expected"] if e.get("law")]
    titles += [e["law"] for item in native["items"] for e in item["expected"] if e.get("law")]
    return titles


def prepare(
    keyword: dict[str, Any], native: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """合成題單：每題 `{id, kind, cls, split, query, level, answer}`；另回類型後綴。"""
    out: list[dict[str, Any]] = []
    for item in keyword["items"]:
        kind = {"definition": "kw_definition", "absent": "kw_absent", "renamed": "renamed"}[
            item["bucket"]
        ]
        if kind == "renamed":
            level, answer = "law", [e["pcode"] for e in item["expected"]]
        else:
            level, answer = "article", [e["article"] for e in item["expected"]]
        out.append(_row(f"kw:{item['id']}", kind, item["split"], item["query"], level, answer))
    for item in native["items"]:
        answer = [e["article"] for e in item["expected"]]
        out.append(
            _row(
                f"nat:{item['id']}",
                item["kind"],
                item["split"],
                item["question"],
                "article",
                answer,
            )
        )
    suffixes = type_suffixes(law_titles(keyword, native))
    return out + absent_names(out, suffixes), suffixes


def _row(
    ident: str, kind: str, split: str, query: str, level: str, answer: list[str]
) -> dict[str, Any]:
    return {
        "id": ident,
        "kind": kind,
        "cls": CLASS_OF[kind],
        "split": split,
        "query": query,
        "level": level,
        "answer": answer,
    }


def collect(
    queries: Sequence[Mapping[str, Any]], address: str, workers: int, skip: set[str]
) -> int:
    """每題一次 `SearchArticles`，一題一行寫到標準輸出。"""
    import socket

    import grpc

    from envcite.ontology.v2 import ontology_pb2 as pb
    from envcite.ontology.v2 import ontology_pb2_grpc as grpc_api

    stub = grpc_api.OntologyServiceStub(grpc.insecure_channel(address))
    pod = socket.gethostname()
    todo = [q for q in queries if q["id"] not in skip]

    def ask(q: Mapping[str, Any]) -> dict[str, Any]:
        hits: list[dict[str, Any]] = []
        for attempt in range(4):
            res = stub.SearchArticles(
                pb.SearchArticlesRequest(query=q["query"], limit=10, include_guidance=True),
                timeout=60,
            )
            hits = [
                {
                    "a": f"{h.identifier.local_id}-art-{h.number}",
                    "p": h.identifier.local_id,
                    "lx": round(h.lexical_score, 4),
                    "se": round(h.semantic_similarity, 4)
                    if h.HasField("semantic_similarity")
                    else None,
                    "co": round(h.term_coverage, 4),
                    "gd": h.guidance_documents,
                }
                for h in res.hits
            ]
            if not hits or any(h["se"] is not None for h in hits):
                return _record(q, hits, pod, skipped=False)
            time.sleep(1.0 + attempt)
        return _record(q, hits, pod, skipped=True)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(ask, todo):
            sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            done += 1
    print(f"收集 {done} 題（略過已完成 {len(skip)}）", file=sys.stderr)
    return 0


def _record(
    q: Mapping[str, Any], hits: list[dict[str, Any]], pod: str, *, skipped: bool
) -> dict[str, Any]:
    return {"id": q["id"], "pod": pod, "semantic_skipped": skipped, "hits": hits}


#: 語意下界的格子：共用的 0.71 與往下放寬的幾檔。
SEMANTIC_GRID = (SEMANTIC_FLOOR, 0.68, 0.65, 0.6)
#: 比共用下界更嚴的幾檔：只用 `compare-rules` 兩兩比較預先登記的候選，`choose` 不搜尋它們。
STRICT_SEMANTIC_GRID = (0.74, 0.77, 0.8, 0.85)


def _rule_name(theta: float, b: bool, semantic: float) -> str:
    base = f"θ{theta:g}{'+B' if b else ''}"
    return base if semantic == SEMANTIC_FLOOR else f"{base}/s{semantic:g}"


#: 規則的格子：覆蓋率門檻 × 語意下界。
#: 「只靠語意進來的命中要含至少一個查詢詞」（候選 B）不在格子裡：它在舊名題上只減不增
#: （`keyword-renamed-2026-11-13.md`），而短關鍵字的同義說法量不到，不能用題庫看不到傷害來放行。
#: `passes` 仍保留這個參數，供重現先前的比較。
RULES: dict[str, tuple[float, bool, float]] = {
    _rule_name(theta, False, semantic): (theta, False, semantic)
    for semantic in (*SEMANTIC_GRID, *STRICT_SEMANTIC_GRID)
    for theta in (0.0, 0.2, 0.34, 0.5, 0.7)
}
#: 現行上線的規則：覆蓋率 0.5，加整詞命中放行。
SHIPPED = "θ0.5"

Hit = Mapping[str, Any]
Case = dict[str, Any]


def passes(
    hit: Hit, theta: float, semantic_needs_term: bool, semantic_floor: float = SEMANTIC_FLOOR
) -> bool:
    """平台列表規則：函釋帶出、語意達下界、字面達下界且覆蓋率達門檻，或整詞命中。"""
    if hit["gd"] > 0:
        return True
    if (hit["se"] or 0.0) >= semantic_floor and (hit["co"] > 0 or not semantic_needs_term):
        return True
    if hit["lx"] >= LEXICAL_FLOOR and hit["co"] >= theta:
        return True
    return hit["lx"] > 0 and hit["co"] >= 1.0


def outcome(case: Case, rule: str) -> tuple[bool | None, int]:
    """（可答題是否列出答案；無答案題為 None，列出幾條）。"""
    theta, b, semantic = RULES[rule]
    kept = [h for h in case["hits"] if passes(h, theta, b, semantic)]
    answer = set(case["answer"])
    if not answer:
        return None, len(kept)
    key = "p" if case["level"] == "law" else "a"
    return any(h[key] in answer for h in kept), len(kept)


def tally(results: Sequence[tuple[bool | None, int]]) -> dict[str, int]:
    out = {"answerable": 0, "found": 0, "size": 0, "absent": 0, "leak": 0}
    for ok, n in results:
        if ok is None:
            out["absent"] += 1
            out["leak"] += n > 0
        else:
            out["answerable"] += 1
            out["found"] += ok
            out["size"] += n
    return out


#: 放寬語意下界時，新的下界要高過該組無答案題的最高語意相似度至少這麼多。
#: 沒有這道邊際，選出來的下界會貼著開發集的最高值，換一批題就漏放（開發集實測差 0.001）。
SEMANTIC_MARGIN = 0.03


def absent_top_similarity(cases: Sequence[Case]) -> float:
    """一組題裡，無答案題前 10 條中最高的語意相似度。"""
    return max(
        (max((h["se"] or 0.0 for h in c["hits"]), default=0.0) for c in cases if not c["answer"]),
        default=0.0,
    )


#: 找到答案的題數落在最佳值的這個比例內，就視為同樣好，改比誰列得少、改的參數少。
FOUND_TOLERANCE = 0.01


def changes(rule: str) -> int:
    """這條規則與現行規則有幾個參數不同。"""
    return sum(a != b for a, b in zip(RULES[rule], RULES[SHIPPED], strict=True))


def choose(cases: Sequence[Case]) -> str:
    """一組題裡選規則。

    可選的規則：無答案題的誤放與找到答案的題數都不得比現行規則差；語意下界要放寬到共用值以下時，須高過該組無答案題
    最高語意相似度加 `SEMANTIC_MARGIN`。可選的裡面，找到答案的題數落在最佳值的 `FOUND_TOLERANCE`
    內的算同樣好，其中取列得最少的，再取與現行規則差得最少的。
    """
    base = tally([outcome(c, SHIPPED) for c in cases])
    top = absent_top_similarity(cases)
    admissible: list[tuple[str, dict[str, int]]] = []
    for rule, (_, _, semantic) in RULES.items():
        if semantic > SEMANTIC_FLOOR:
            continue
        if semantic < SEMANTIC_FLOOR and semantic < top + SEMANTIC_MARGIN:
            continue
        t = tally([outcome(c, rule) for c in cases])
        if t["leak"] <= base["leak"] and t["found"] >= base["found"]:
            admissible.append((rule, t))
    if not admissible:
        return SHIPPED
    best = max(t["found"] for _, t in admissible)
    slack = round(FOUND_TOLERANCE * admissible[0][1]["answerable"])
    near = [(rule, t) for rule, t in admissible if t["found"] >= best - slack]
    return min(near, key=lambda x: (x[1]["size"], changes(x[0]), -x[1]["found"]))[0]


def classifiers(suffixes: Sequence[str]) -> dict[str, Callable[[Case], str]]:
    """候選的分類器：只看查詢字串（平台拿得到的），與「型別已知」的上限。"""

    def shape(length: int, *, mark: bool = True) -> Callable[[Case], str]:
        """結尾是類型後綴的是名稱；其餘依句尾問號（`mark`）或長度分問句與關鍵字。"""

        def f(case: Case) -> str:
            q = case["query"]
            body = q.rstrip("？?")
            if any(body.endswith(s) for s in suffixes):
                return "name"
            asked = mark and q != body
            return "question" if asked or len(body) >= length else "term"

        return f

    out: dict[str, Callable[[Case], str]] = {
        "single": lambda c: "all",
        "oracle": lambda c: c["cls"],
    }
    for length in (8, 10, 12, 14, 16, 20):
        out[f"length>={length}"] = lambda c, n=length: "long" if len(c["query"]) >= n else "short"
    for length in (8, 10, 12):
        out[f"shape(len>={length})"] = shape(length)
    for length in (10, 12, 14):
        out[f"shape-no-mark(len>={length})"] = shape(length, mark=False)
    return out


def sign_p(gain: int, loss: int) -> float:
    """單向符號檢定：增加的題數不比減少的多的機率（兩邊各半）。"""
    from math import comb

    n = gain + loss
    return sum(comb(n, k) for k in range(gain, n + 1)) / 2**n if n else 1.0


def run_router(
    cases: Sequence[Case], classify: Callable[[Case], str], chosen: Mapping[str, str]
) -> list[tuple[bool | None, int]]:
    return [outcome(c, chosen.get(classify(c), SHIPPED)) for c in cases]


def compare(
    cases: Sequence[Case], classify: Callable[[Case], str], chosen: Mapping[str, str]
) -> dict[str, Any]:
    """路由對現行規則：分真實型別的找到率、列得條數、誤放，與逐題的增減。"""
    base = [outcome(c, SHIPPED) for c in cases]
    routed = run_router(cases, classify, chosen)
    by: dict[str, Any] = {}
    for cls in sorted({c["cls"] for c in cases}):
        idx = [i for i, c in enumerate(cases) if c["cls"] == cls]
        by[cls] = {
            "shipped": tally([base[i] for i in idx]),
            "routed": tally([routed[i] for i in idx]),
        }
    gain = sum(1 for (a, _), (b, _) in zip(base, routed, strict=True) if a is False and b is True)
    loss = sum(1 for (a, _), (b, _) in zip(base, routed, strict=True) if a is True and b is False)
    leaked_more = sum(
        1 for (a, x), (b, y) in zip(base, routed, strict=True) if a is None and x == 0 and y > 0
    )
    leaked_less = sum(
        1 for (a, x), (b, y) in zip(base, routed, strict=True) if a is None and x > 0 and y == 0
    )
    return {
        "by_class": by,
        "gain": gain,
        "loss": loss,
        "p_gain": sign_p(gain, loss),
        "leak_more": leaked_more,
        "leak_less": leaked_less,
    }


def evaluate(
    cases: Sequence[Case],
    suffixes: Sequence[str],
    choose_on: str,
    report_on: str,
    only: str | None,
    fixed: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """規則只在 `choose_on` 的題上選；結果報在 `report_on`（同一批時是開發集內的結果）。

    `fixed` 是已定案的各組規則：不再選，直接套用（報測試集、或檢驗定案的規則換分類器後是否穩）。
    """
    pick = [c for c in cases if c["split"] == choose_on]
    report = [c for c in cases if c["split"] == report_on]
    result: dict[str, Any] = {"suffixes": list(suffixes), "classifiers": {}}
    # 規則格子在各真實型別上的取捨：路由的依據
    result["tradeoff"] = {
        cls: {rule: tally([outcome(c, rule) for c in report if c["cls"] == cls]) for rule in RULES}
        for cls in sorted({c["cls"] for c in report})
    }
    for name, classify in classifiers(suffixes).items():
        if only and name != only:
            continue
        groups: dict[str, list[Case]] = defaultdict(list)
        for c in pick:
            groups[classify(c)].append(c)
        chosen = dict(fixed) if fixed else {g: choose(cs) for g, cs in groups.items()}
        agree = sum(1 for c in report if classify(c) == c["cls"]) / len(report)
        result["classifiers"][name] = {
            "chosen": chosen,
            "group_sizes": {g: len(cs) for g, cs in groups.items()},
            "agrees_with_true_class": agree,
            **compare(report, classify, chosen),
        }
    return result


def load_cases(queries_path: Path, hits_path: Path) -> list[Case]:
    queries = {q["id"]: q for q in json.loads(queries_path.read_text(encoding="utf-8"))}
    cases: list[Case] = []
    for line in hits_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        cases.append(
            {**queries[rec["id"]], "hits": rec["hits"], "skipped": rec["semantic_skipped"]}
        )
    return cases


def render(result: Mapping[str, Any], report_on: str) -> str:
    """人讀的表：各型別的規則取捨、各分類器選出的路由與結果。"""

    def pct(a: int, b: int) -> str:
        return "—" if not b else f"{a / b:.3f}"

    lines = [
        f"# 查詢分類與路由（{report_on}）",
        "",
        f"類型後綴：{'、'.join(result['suffixes'])}",
        "",
    ]
    lines += ["## 規則在各型別上的取捨", ""]
    lines += ["| 型別 | 規則 | 找到答案 | 平均列出 | 無答案誤放 |", "|---|---|---|---|---|"]
    for cls, rules in result["tradeoff"].items():
        for rule, t in rules.items():
            size = "—" if not t["answerable"] else f"{t['size'] / t['answerable']:.2f}"
            lines.append(
                f"| {cls}（{t['answerable']}／{t['absent']}） | {rule} | "
                f"{pct(t['found'], t['answerable'])} | {size} | {pct(t['leak'], t['absent'])} |"
            )
    lines += ["", "## 分類器與路由", ""]
    for name, r in result["classifiers"].items():
        lines += [f"### {name}", ""]
        lines.append(
            f"- 與真實型別一致：{r['agrees_with_true_class']:.3f}；各組選出的規則：{r['chosen']}"
            f"（組大小 {r['group_sizes']}）"
        )
        lines.append(
            f"- 對現行規則：找到答案 +{r['gain']}／−{r['loss']}（單向 p {r['p_gain']:.2g}）；"
            f"無答案誤放 +{r['leak_more']}／−{r['leak_less']}"
        )
        lines += [
            "",
            "| 真實型別 | 現行找到 | 路由找到 | 現行誤放 | 路由誤放 |",
            "|---|---|---|---|---|",
        ]
        for cls, v in r["by_class"].items():
            s, t = v["shipped"], v["routed"]
            lines.append(
                f"| {cls} | {pct(s['found'], s['answerable'])} | {pct(t['found'], t['answerable'])}"
                f" | {pct(s['leak'], s['absent'])} | {pct(t['leak'], t['absent'])} |"
            )
        lines.append("")
    return "\n".join(lines)


#: 上線的路由：分類器與各型別的規則。與平台 `LawSearchOptions` 的預設一致
#: （關鍵字：覆蓋率 0.7、語意下界 0.74；問句：語意下界 0.68；名稱：現行）。
DEPLOYED_CLASSIFIER = "shape(len>=10)"
DEPLOYED_ROUTE = {"term": "θ0.7/s0.74", "name": "θ0.5", "question": "θ0.5/s0.68"}
LIVE_LIMIT = 10


def predicted_ids(case: Case, classify: Callable[[Case], str]) -> list[str]:
    """上線的路由套在離線收集的命中上，預測 `/v2/search` 會回哪些條文（依回傳順序，最多 10 條）。"""
    theta, b, semantic = RULES[DEPLOYED_ROUTE.get(classify(case), SHIPPED)]
    kept = [h["a"] for h in case["hits"] if passes(h, theta, b, semantic)]
    return kept[:LIVE_LIMIT]


def live_ids(hits: Sequence[Mapping[str, Any]]) -> list[str]:
    """`/v2/search` 回應的條文代號，與離線的 `a` 欄同一個寫法。"""
    return [f"{h['identifier']['localId']}-art-{h['number']}" for h in hits]


def compare_lists(live: Sequence[str], predicted: Sequence[str]) -> str:
    """identical：同一串；same-set：同一批、順序不同；differs：不同。"""
    if list(live) == list(predicted):
        return "identical"
    return "same-set" if sorted(live) == sorted(predicted) else "differs"


def stratified_sample(cases: Sequence[Case], n: int) -> list[Case]:
    """依雜湊排序後，各題型輪流取，每個題型都出現；與題庫增減無關。"""
    import hashlib

    by_kind: dict[str, list[Case]] = defaultdict(list)
    for c in sorted(cases, key=lambda c: hashlib.sha256(c["id"].encode()).hexdigest()):
        by_kind[c["kind"]].append(c)
    out: list[Case] = []
    queues = [by_kind[k] for k in sorted(by_kind)]
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def fetch_live(host: str, key: str, query: str) -> list[dict[str, Any]]:
    """對 `/v2/search` 查一次（查詢全文不寫日誌；key 只放在標頭）。"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{host.rstrip('/')}/v2/search",
        data=json.dumps({"query": query, "asOf": None, "limit": None}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(explain_http_error(error.code)) from None


#: 線上查詢的錯誤碼對應的處理（`docs/tenants.md` 的「出錯對照」）。
HTTP_HINTS = {
    401: "key 不被接受：貼錯、已撤銷或已過期；到該租戶的 API keys 區確認前綴，必要時重發",
    402: "租戶沒有方案；先在營運者後台指派方案，再簽發 key",
    403: "key 沒有 ontology:read 範圍",
    429: "超過用量或速率限制；稍後再試或降低 --sample",
}


def clean_key(raw: str) -> str:
    """去掉貼上時夾帶的東西：貼上標記（ESC[200~）、控制字元、空白換行，與多貼的 `Bearer `。"""
    import re

    key = re.sub(r"\x1b\[[0-9;]*[~A-Za-z]", "", raw).strip().removeprefix("Bearer ")
    return "".join(ch for ch in key if ch.isprintable() and not ch.isspace())


def explain_http_error(code: int) -> str:
    """一句話的錯誤說明，查詢全文與 key 都不出現在裡面。"""
    return f"線上回 HTTP {code}：" + HTTP_HINTS.get(code, "非預期的錯誤；查 platform 日誌")


def live_check(
    cases: Sequence[Case], suffixes: Sequence[str], host: str, key: str, sample: int
) -> dict[str, Any]:
    """對線上 `/v2/search` 逐題查詢，與離線預測比對。"""
    classify = classifiers(suffixes)[DEPLOYED_CLASSIFIER]
    rows: list[dict[str, Any]] = []
    for case in stratified_sample(cases, sample):
        live = live_ids(fetch_live(host, key, case["query"]))
        want = predicted_ids(case, classify)
        rows.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "query": case["query"],
                "class": classify(case),
                "live": len(live),
                "predicted": len(want),
                "match": compare_lists(live, want),
            }
        )
    by_match = Counter(r["match"] for r in rows)
    return {"summary": dict(by_match), "total": len(rows), "rows": rows}


def render_live(result: Mapping[str, Any]) -> str:
    lines = [f"共 {result['total']} 題：{result['summary']}", ""]
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    for r in result["rows"]:
        by_kind[r["kind"]][r["match"]] += 1
    lines += ["| 題型 | identical | same-set | differs |", "|---|---|---|---|"]
    for kind, c in sorted(by_kind.items()):
        lines.append(f"| {kind} | {c['identical']} | {c['same-set']} | {c['differs']} |")
    differing = [r for r in result["rows"] if r["match"] == "differs"]
    if differing:
        lines += [
            "",
            "不同的題（前 10）：",
            "",
            "| 題型 | 查詢 | 線上條數 | 預測條數 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r['kind']} | {r['query']} | {r['live']} | {r['predicted']} |"
            for r in differing[:10]
        ]
    return "\n".join(lines)


def compare_rules(cases: Sequence[Case], a: str, b: str) -> dict[str, Any]:
    """同一批題上兩條規則逐題配對，分題型：找到、誤放與列出條數的增減。"""
    out: dict[str, Any] = {}
    for kind in sorted({c["kind"] for c in cases}):
        group = [c for c in cases if c["kind"] == kind]
        ra = [outcome(c, a) for c in group]
        rb = [outcome(c, b) for c in group]
        pairs = list(zip(ra, rb, strict=True))
        gain = sum(1 for (x, _), (y, _) in pairs if x is False and y is True)
        loss = sum(1 for (x, _), (y, _) in pairs if x is True and y is False)
        leak_more = sum(1 for (x, n), (y, m) in pairs if x is None and n == 0 and m > 0)
        leak_less = sum(1 for (x, n), (y, m) in pairs if x is None and n > 0 and m == 0)
        shorter = sum(1 for (_, n), (_, m) in pairs if m < n)
        longer = sum(1 for (_, n), (_, m) in pairs if m > n)
        out[kind] = {
            "a": tally(ra),
            "b": tally(rb),
            "gain": gain,
            "loss": loss,
            "leak_more": leak_more,
            "leak_less": leak_less,
            "shorter": shorter,
            "longer": longer,
            "p_fewer_leaks": sign_p(leak_less, leak_more),
            "p_shorter": sign_p(shorter, longer),
        }
    return out


def render_compare(result: Mapping[str, Any], a: str, b: str) -> str:
    lines = [
        f"| 題型 | 可答／無答案 | 找到 {a} → {b}（增／減） | 誤放 {a} → {b}（少／多） | "
        f"平均列出 {a} → {b} | 列表變短／變長 |",
        "|---|---|---|---|---|---|",
    ]
    for kind, r in result.items():
        ta, tb = r["a"], r["b"]
        size = (
            "—"
            if not ta["answerable"]
            else f"{ta['size'] / ta['answerable']:.2f} → {tb['size'] / tb['answerable']:.2f}"
        )
        leak = f"{ta['leak']} → {tb['leak']}（−{r['leak_less']}／+{r['leak_more']}"
        lines.append(
            f"| {kind} | {ta['answerable']}／{ta['absent']} "
            f"| {ta['found']} → {tb['found']}（+{r['gain']}／−{r['loss']}） "
            f"| {leak}，p {r['p_fewer_leaks']:.2g}） "
            f"| {size} | {r['shorter']}／{r['longer']}（p {r['p_shorter']:.2g}） |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    lc = sub.add_parser("live-check", help="對線上 /v2/search 查詢，與離線預測逐題比對")
    lc.add_argument("queries", type=Path)
    lc.add_argument("hits", type=Path)
    lc.add_argument("--meta", type=Path, required=True)
    lc.add_argument("--split", required=True, help="題單的切分名（dev、test、fresh2…）")
    lc.add_argument("--host", required=True, help="如 https://主機名稱")
    lc.add_argument("--sample", type=int, default=60)
    lc.add_argument("--json-out", type=Path)
    cr = sub.add_parser("compare-rules", help="兩條規則在同一批題上逐題配對")
    cr.add_argument("queries", type=Path)
    cr.add_argument("hits", type=Path)
    cr.add_argument("--a", default=SHIPPED)
    cr.add_argument("--b", required=True)
    cr.add_argument("--split", default="fresh")
    cr.add_argument("--cls", help="只比較這一型（term、question、name）")
    cr.add_argument("--json-out", type=Path)
    a = sub.add_parser("prepare")
    a.add_argument("--keyword", type=Path, required=True)
    a.add_argument("--native", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--meta", type=Path, required=True, help="類型後綴等衍生資料")
    e = sub.add_parser("evaluate")
    e.add_argument("queries", type=Path)
    e.add_argument("hits", type=Path)
    e.add_argument("--meta", type=Path, required=True)
    e.add_argument("--report-on", default="dev", help="dev、test，或新題批次名（fresh、fresh2…）")
    e.add_argument(
        "--classifier",
        help="只跑這一個分類器；報告測試集時必填（定案之後才量，只報一次）",
    )
    e.add_argument(
        "--rules",
        help="已定案的各組規則，如 term=θ0.7,question=θ0.5/s0.68,name=θ0.5；給了就不再選",
    )
    e.add_argument("--json-out", type=Path)
    c = sub.add_parser("collect")
    c.add_argument("queries", type=Path)
    c.add_argument("--address", default="localhost:50051")
    c.add_argument("--workers", type=int, default=2, help="不得超過服務的嵌入名額（2）")
    c.add_argument("--skip", type=Path, help="已完成的題號（一行一個）")
    args = p.parse_args(argv)

    if args.command == "prepare":
        rows, suffixes = prepare(
            json.loads(args.keyword.read_text(encoding="utf-8")),
            json.loads(args.native.read_text(encoding="utf-8")),
        )
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        args.meta.write_text(json.dumps({"suffixes": suffixes}, ensure_ascii=False) + "\n", "utf-8")
        by: defaultdict[tuple[str, str], int] = defaultdict(int)
        for r in rows:
            by[(r["cls"], r["split"])] += 1
        print(f"{len(rows)} 題：{dict(by)}；類型後綴 {suffixes}", file=sys.stderr)
        return 0
    if args.command == "live-check":
        key = clean_key(
            os.environ.get("ENVCITE_API_KEY") or getpass.getpass("API key（不會顯示）：")
        )
        if not key:
            raise SystemExit("沒有收到 API key：貼上後要按 Enter（輸入時不會顯示）")
        print(f"收到 key，長度 {len(key)}（只印長度）", file=sys.stderr)
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        cases = [c for c in load_cases(args.queries, args.hits) if c["split"] == args.split]
        result = live_check(cases, meta["suffixes"], args.host, key, args.sample)
        if args.json_out:
            args.json_out.write_text(
                json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8"
            )
        print(render_live(result))
        return 0
    if args.command == "compare-rules":
        if not {args.a, args.b} <= set(RULES):
            p.error(f"不認得的規則：{sorted({args.a, args.b} - set(RULES))}")
        cases = [
            c
            for c in load_cases(args.queries, args.hits)
            if c["split"] == args.split and (not args.cls or c["cls"] == args.cls)
        ]
        result = compare_rules(cases, args.a, args.b)
        if args.json_out:
            args.json_out.write_text(
                json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8"
            )
        print(render_compare(result, args.a, args.b))
        return 0
    if args.command == "evaluate":
        if args.report_on == "test" and not args.classifier:
            p.error("報告測試集要用 --classifier 指定定案的分類器")
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        cases = load_cases(args.queries, args.hits)
        fixed = dict(kv.split("=", 1) for kv in args.rules.split(",")) if args.rules else None
        if fixed and not set(fixed.values()) <= set(RULES):
            p.error(f"不認得的規則：{sorted(set(fixed.values()) - set(RULES))}")
        result = evaluate(cases, meta["suffixes"], "dev", args.report_on, args.classifier, fixed)
        if args.json_out:
            args.json_out.write_text(
                json.dumps(result, ensure_ascii=False, indent=1) + "\n", "utf-8"
            )
        print(render(result, args.report_on))
        return 0
    skip = set(args.skip.read_text(encoding="utf-8").split()) if args.skip else set()
    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    return collect(queries, args.address, min(args.workers, 2), skip)


# 讓 evaluate 的型別提示不必在這一段就引入
Policy = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]

if __name__ == "__main__":
    raise SystemExit(main())
