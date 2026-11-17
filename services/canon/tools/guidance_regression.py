"""函釋入口的回歸檢查：用函釋自己的詞問，看條文有沒有出現在最後的名單裡（唯讀）。

    # 題庫在 eval/datasets/guidance-regression-<日期>.json，量測結果在 eval/results/
    uv run python tools/guidance_regression.py build  --out <題庫>
    uv run python tools/guidance_regression.py verify <題庫>
    uv run python tools/guidance_regression.py measure <題庫> --address localhost:50051 \\
        --out <結果>.run.json
    uv run python tools/guidance_regression.py compare <之前>.run.json <之後>.run.json

條文寫「燃燒」、函釋寫「露天燃燒」：查詢的詞常是函釋的，條文裡沒有，字面與語意兩臂都撈不到，只有函釋入口
找得到。入口到最後名單之間有好幾關（匹配函釋、錨定到條文、展開處罰鏈、與兩臂融合、截斷），每一關改了都
可能讓原本找得到的條文消失或掉到截斷線外；原生題庫（`native_golden.py`）沒有這類題，所以這裡補一份。

**題目由規則從資料產生，不用模型：**

- 查詢詞：函釋主旨裡的 4 字詞，且出現在 3 到 30 件函釋的主旨（夠有鑑別力、又不是只有一件的巧合），
  並且條文全文沒有這個詞（字面臂撈得到就不算函釋入口的題）。
- 預期條文：這些函釋錨定的條文中，至少 60%（且至少 2 件）的函釋共同指向的，加上本體論
  （`annotation.current_violation`）沿罰則往下走到的條文：處罰它的罰則條，與處罰這些罰則的條
  （最多 3 層）。錨定由公文內文的引用規則抽出（`law/guidance_anchor.py`），罰則邊由條文的引用規則
  抽出（`violation.py`），都不是被測的檢索；鏈在這裡用 Python 重算，不呼叫
  `retrieval.sanction_chain`，所以展開的 SQL 改壞時這裡看得出來。

**題目只由上述規則從資料產生，沒有任何人工指定的題目、詞或答案，也沒有可以填人工內容的欄位。**
三道守門擋掉手寫與作弊：`shape_errors` 固定欄位並驗題號是詞的雜湊（`measure` 先過這關）；`verify`
把每題用規則對資料重算，與題庫不同就是被改過或手加的；`tests/test_guidance_regression.py` 掃本檔，
不准有寫死的 4 字詞。

**這量的是管線，不是相關性。** 預期條文就是入口該帶出的，所以入口正常時幾乎全中；它的用處是回歸：
改了展開、融合、截斷、錨定之後，之前找得到的題現在找不到，`compare` 會列出來。不要拿它的命中率當
品質指標，也不能用它來調參數。

`build` 讀資料庫（`ENVCITE_CANON_DSN`，唯讀即可）；`measure` 經 gRPC 打真正的服務，所以包含詞典、
展開、融合與（`--rerank`）精排。
"""

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import grpc

import golden_set as gs
from canon import vocabulary
from canon.db import Conn, fetch
from canon.settings import DatabaseSettings, load
from canon.store.canon_store import CanonStore
from envcite.canon.v1 import canon_pb2, canon_pb2_grpc

SCHEMA = "envcite-guidance-regression/1"
#: 查詢詞的字數、出現的函釋件數範圍，與預期條文要有多少比例的函釋共同指向。
NGRAM = 4
MIN_DOCS = 3
MAX_DOCS = 30
CONSENSUS = 0.6
#: 罰則鏈的層數上限，與 `retrieval.sanction_chain` 一致。
CHAIN_DEPTH = 3
#: 題目的欄位，只有這些，且都由規則推導；沒有人工填寫的欄位（`shape_errors` 擋）。
CASE_FIELDS = ("id", "query", "documents", "anchored", "expected")
#: 題數與抽樣種子；規則固定時，同一份資料產生同一份題庫。
CASES = 150
SEED = 0
#: 字面臂撈得到的條文數：查詞在條文裡出現就會排在前面，取這麼多條檢查有沒有逐字出現。
LITERAL_PROBE = 50
TOP = 10

_HAN_RUN = re.compile(r"[一-鿿]+")

#: （法規代碼，條號）。
type Article = tuple[str, str]


class _Anchor(NamedTuple):
    document_id: int
    pcode: str
    number: str


class _Subject(NamedTuple):
    id: int
    subject: str


def ngrams(subject: str, size: int = NGRAM) -> set[str]:
    """主旨裡連續漢字的 `size` 字詞；標點、數字、英文把詞切斷，不跨過去。"""
    return {
        run[i : i + size] for run in _HAN_RUN.findall(subject) for i in range(len(run) - size + 1)
    }


def consensus(
    anchors: Mapping[int, set[Article]], documents: Sequence[int], share: float = CONSENSUS
) -> list[Article]:
    """多數函釋共同指向的條文：至少 `share` 比例、且至少 2 件的函釋錨定到它。"""
    needed = max(2, math.ceil(share * len(documents)))
    votes = Counter(article for d in documents for article in anchors.get(d, ()))
    return sorted(article for article, n in votes.items() if n >= needed)


def candidates(subjects: Mapping[int, str]) -> dict[str, list[int]]:
    """主旨出現在 `MIN_DOCS` 到 `MAX_DOCS` 件函釋的 4 字詞，與它出現的函釋（依編號）。"""
    found: dict[str, list[int]] = defaultdict(list)
    for document, subject in sorted(subjects.items()):
        for phrase in ngrams(subject):
            found[phrase].append(document)
    return {p: ds for p, ds in found.items() if MIN_DOCS <= len(ds) <= MAX_DOCS}


def closure(
    edges: Mapping[Article, set[Article]], start: Iterable[Article], depth: int = CHAIN_DEPTH
) -> list[Article]:
    """沿罰則邊往下走：`edges[條]` 是處罰該條的罰則條；回傳 `start` 加上 `depth` 層內到得了的條。"""
    reached = set(start)
    frontier = set(reached)
    for _ in range(depth):
        frontier = {p for article in frontier for p in edges.get(article, ())} - reached
        reached |= frontier
    return sorted(reached)


def case_id(phrase: str) -> str:
    """題號：詞的雜湊，題庫重建時同一個詞同一個號碼。"""
    return "g-" + hashlib.sha1(phrase.encode()).hexdigest()[:8]


def found_in(
    expected: Iterable[Article], returned: Sequence[Article], k: int | None = None
) -> list[Article]:
    """預期條文中，出現在前 `k` 條（`None` 為全部）的。"""
    window = set(returned if k is None else returned[:k])
    return [a for a in expected if a in window]


def regressions(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """（之前找得到、現在少了的題，之前沒有、現在多了的題）；以預期條文的命中數比。"""
    lost, gained = [], []
    for case, was in before["cases"].items():
        now = after["cases"].get(case)
        if now is None:
            continue
        if len(now["found"]) < len(was["found"]):
            lost.append(case)
        elif len(now["found"]) > len(was["found"]):
            gained.append(case)
    return sorted(lost), sorted(gained)


# --- build --------------------------------------------------------------------------------------

_DOCUMENTS = """
    SELECT d.id, d.subject
      FROM guidance.document AS d
     WHERE NOT EXISTS (SELECT 1 FROM guidance.cessation AS c
                        WHERE c.document_id = d.id AND c.ceased_on <= current_date)
       AND EXISTS (SELECT 1 FROM guidance.interpretation AS i WHERE i.document_id = d.id)
"""

_ANCHORS = """
    SELECT i.document_id, r.local_id, corpus.article_number(p.path)
      FROM guidance.interpretation AS i
      JOIN corpus.provision_version AS v
        ON v.provision_id = i.provision_id AND v.content_sha256 = i.content_sha256
      JOIN corpus.provision AS p ON p.id = v.provision_id
      JOIN corpus.legal_resource AS r ON r.id = p.resource_id
     WHERE v.superseded_at IS NULL AND v.force_period @> current_date
"""


#: 本體論的罰則邊：（被罰的義務條）→（處罰它的罰則條），只取現行的條文。
_PENALTIES = """
    SELECT cr.local_id AS duty_pcode, corpus.article_number(cp.path) AS duty_number,
           pr.local_id AS penalty_pcode, corpus.article_number(pp.path) AS penalty_number
      FROM annotation.current_violation AS vi
      JOIN corpus.provision AS cp ON cp.id = vi.cited_provision_id
      JOIN corpus.legal_resource AS cr ON cr.id = cp.resource_id
      JOIN corpus.provision AS pp ON pp.id = vi.provision_id
      JOIN corpus.legal_resource AS pr ON pr.id = pp.resource_id
      JOIN corpus.provision_version AS v
        ON v.provision_id = vi.provision_id AND v.content_sha256 = vi.content_sha256
     WHERE v.superseded_at IS NULL AND v.force_period @> current_date
"""


class _Penalty(NamedTuple):
    duty_pcode: str
    duty_number: str
    penalty_pcode: str
    penalty_number: str


def _literal_probe(store: CanonStore) -> Callable[[str], bool]:
    return lambda phrase: _literal(store, phrase)


def _literal(store: CanonStore, phrase: str) -> bool:
    """這個詞是否逐字出現在現行條文：字面臂搜它，前 `LITERAL_PROBE` 條有沒有哪一條含有它。"""
    current = vocabulary.serving(store.retrieval)
    terms = current.segmenter.to_tsvector_input(phrase)
    hits = store.retrieval.search(
        terms,
        tokenizer=current.segmenter.tokenizer_id,
        as_of=date.today().isoformat(),
        limit=LITERAL_PROBE,
    )
    return any(phrase in hit.content for hit in hits)


class Derivation(NamedTuple):
    """題目只由這三樣資料推導：函釋主旨、函釋錨定的條文、本體論的罰則邊。"""

    subjects: Mapping[int, str]
    anchors: Mapping[int, set[Article]]
    edges: Mapping[Article, set[Article]]


def derive(conn: Conn) -> Derivation:
    subjects = {row.id: row.subject for row in fetch(conn, _DOCUMENTS, None, _Subject)}
    anchors: dict[int, set[Article]] = defaultdict(set)
    for row in fetch(conn, _ANCHORS, None, _Anchor):
        anchors[row.document_id].add((row.pcode, row.number))
    edges: dict[Article, set[Article]] = defaultdict(set)
    for edge in fetch(conn, _PENALTIES, None, _Penalty):
        penalty = (edge.penalty_pcode, edge.penalty_number)
        if penalty != (edge.duty_pcode, edge.duty_number):
            edges[(edge.duty_pcode, edge.duty_number)].add(penalty)
    return Derivation(subjects, anchors, edges)


def derive_case(
    phrase: str, source: Derivation, literal: Callable[[str], bool]
) -> dict[str, Any] | None:
    """規則對一個詞的判定：成題就回傳整題，不成題回傳 `None`。題只能從這裡來。

    `literal(詞)` 是這個詞是否逐字出現在現行條文；出現就不算函釋入口的題。
    """
    if len(phrase) != NGRAM or not _HAN_RUN.fullmatch(phrase):
        return None
    documents = candidates_documents(source.subjects, phrase)
    if not MIN_DOCS <= len(documents) <= MAX_DOCS:
        return None
    anchored = consensus(source.anchors, documents)
    if not anchored or literal(phrase):
        return None
    return {
        "id": case_id(phrase),
        "query": phrase,
        "documents": len(documents),
        "anchored": [list(a) for a in anchored],
        "expected": [list(a) for a in closure(source.edges, anchored)],
    }


def build_cases(
    source: Derivation, literal: Callable[[str], bool], count: int = CASES, seed: int = SEED
) -> list[dict[str, Any]]:
    """依規則從資料產生題目：候選詞依種子洗牌，逐個交給 `derive_case`，取前 `count` 個。"""
    pool = sorted(candidates(source.subjects))
    random.Random(seed).shuffle(pool)
    cases: list[dict[str, Any]] = []
    for phrase in pool:
        if case := derive_case(phrase, source, literal):
            cases.append(case)
            if len(cases) == count:
                break
    return sorted(cases, key=lambda c: c["id"])


def candidates_documents(subjects: Mapping[int, str], phrase: str) -> list[int]:
    """主旨含這個詞的函釋（依編號）。"""
    return [d for d, s in sorted(subjects.items()) if phrase in s]


def shape_errors(cases: Sequence[Mapping[str, Any]]) -> list[str]:
    """題目的形狀（不連資料庫）：欄位固定，題號、詞、錨定與預期條文彼此要對得上。

    題目沒有人工填寫的欄位：多一個欄位、題號不是詞的雜湊、詞不是 4 個漢字，都不收。
    """
    errors: list[str] = []
    seen: set[str] = set()
    for case in cases:
        name = str(case.get("id"))
        if set(case) != set(CASE_FIELDS):
            odd = sorted(set(case) ^ set(CASE_FIELDS))
            errors.append(f"{name}：欄位要剛好是 {sorted(CASE_FIELDS)}，多了或少了 {odd}")
            continue
        query = case["query"]
        if len(query) != NGRAM or not _HAN_RUN.fullmatch(query):
            errors.append(f"{name}：詞要是 {NGRAM} 個漢字：{query!r}")
        if case["id"] != case_id(query):
            errors.append(f"{name}：題號不是詞的雜湊")
        if case["id"] in seen:
            errors.append(f"{name}：題號重複")
        seen.add(case["id"])
        if not MIN_DOCS <= case["documents"] <= MAX_DOCS:
            errors.append(f"{name}：函釋件數 {case['documents']} 不在 {MIN_DOCS} 到 {MAX_DOCS}")
        anchored, expected = case["anchored"], case["expected"]
        if not anchored or any(len(a) != 2 for a in anchored + expected):
            errors.append(f"{name}：錨定與預期條文要是（法規代碼，條號）、且錨定不為空")
        elif not {tuple(a) for a in anchored} <= {tuple(e) for e in expected}:
            errors.append(f"{name}：預期條文要含錨定的條文")
    return errors


def verify_cases(
    cases: Sequence[Mapping[str, Any]], source: Derivation, literal: Callable[[str], bool]
) -> list[str]:
    """逐題用規則對資料重算：題庫裡的每一題都要與規則產出的完全相同，否則是被改過或手加的。"""
    errors = shape_errors(cases)
    for case in cases:
        derived = derive_case(str(case.get("query")), source, literal)
        if derived is None:
            errors.append(f"{case.get('id')}：資料與規則產不出這一題（{case.get('query')!r}）")
        elif derived != dict(case):
            errors.append(f"{case.get('id')}：與規則重算的結果不同")
    return errors


def read_cases(path: Path) -> dict[str, Any]:
    """讀題庫（驗雜湊與形狀）；形狀不合的題庫不量。"""
    doc = gs.read(path)
    if errors := shape_errors(doc["cases"]):
        raise SystemExit("題庫形狀不合：\n  " + "\n  ".join(errors))
    return doc


def cmd_build(args: argparse.Namespace) -> None:
    db = load(DatabaseSettings)
    with CanonStore.from_dsn(db.dsn) as store, store.connections.connection() as conn:
        cases = build_cases(derive(conn), _literal_probe(store), args.cases, args.seed)
    doc = gs.write(
        args.out,
        {
            "schema": SCHEMA,
            "rule": (
                f"主旨 {NGRAM} 字詞、出現在 {MIN_DOCS} 到 {MAX_DOCS} 件函釋、條文全文沒有；"
                f"預期＝至少 {CONSENSUS:.0%}（且至少 2 件）的函釋共同錨定的條文，"
                f"加上處罰它們的罰則鏈（{CHAIN_DEPTH} 層內）"
            ),
            "as_of": date.today().isoformat(),
            "seed": args.seed,
            "counts": {"cases": len(cases), "expected": sum(len(c["expected"]) for c in cases)},
            "cases": cases,
        },
    )
    print(f"{doc['counts']}；{args.out}：sha256 {doc['sha256']}")


def cmd_verify(args: argparse.Namespace) -> None:
    doc = read_cases(args.cases)
    db = load(DatabaseSettings)
    with CanonStore.from_dsn(db.dsn) as store, store.connections.connection() as conn:
        errors = verify_cases(doc["cases"], derive(conn), _literal_probe(store))
    for error in errors:
        print(f"  {error}")
    if errors:
        raise SystemExit(f"{len(errors)} 題對不上規則")
    print(f"{len(doc['cases'])} 題都與規則對資料重算的結果相同")


# --- measure ------------------------------------------------------------------------------------


def _search(stub: canon_pb2_grpc.CanonServiceStub, query: str, rerank: bool) -> list[Article]:
    reply = stub.SearchArticles(
        canon_pb2.SearchArticlesRequest(
            query=query, limit=TOP, include_guidance=True, rerank=rerank
        ),
        timeout=60,
    )
    return [(hit.identifier.local_id, hit.number) for hit in reply.hits]


def measure(
    cases: Sequence[Mapping[str, Any]], search: Callable[[str], Sequence[Article]]
) -> dict[str, dict[str, Any]]:
    """每題的名單與預期條文的命中；`search(query)` 回傳（法規代碼，條號）的名單。"""
    out: dict[str, dict[str, Any]] = {}
    for case in cases:
        expected = [(a[0], a[1]) for a in case["expected"]]
        returned = search(case["query"])
        out[case["id"]] = {
            "query": case["query"],
            "expected": [list(a) for a in expected],
            "returned": [list(a) for a in returned],
            "found": [list(a) for a in found_in(expected, returned)],
            "found_top": [list(a) for a in found_in(expected, returned, TOP)],
        }
    return out


def summary(run: Mapping[str, Any]) -> dict[str, float]:
    """題數與命中率：整份預期條文都在名單裡的題、至少一條在的題、預期條文的召回。"""
    cases = list(run["cases"].values())
    n = len(cases)
    expected = sum(len(c["expected"]) for c in cases)
    return {
        "cases": n,
        "all_found": sum(len(c["found"]) == len(c["expected"]) for c in cases) / n,
        "any_found": sum(bool(c["found"]) for c in cases) / n,
        "recall": sum(len(c["found"]) for c in cases) / expected,
        "recall_top": sum(len(c["found_top"]) for c in cases) / expected,
    }


def cmd_measure(args: argparse.Namespace) -> None:
    doc = read_cases(args.cases)
    stub = canon_pb2_grpc.CanonServiceStub(grpc.insecure_channel(args.address))
    cases = measure(doc["cases"], lambda q: _search(stub, q, args.rerank))
    run = {
        "schema": SCHEMA,
        "cases_sha256": doc["sha256"],
        "address": args.address,
        "rerank": args.rerank,
        "cases": cases,
    }
    run["summary"] = summary(run)
    gs.write(args.out, run)
    print(json.dumps(run["summary"], ensure_ascii=False))


def cmd_compare(args: argparse.Namespace) -> None:
    before, after = gs.read(args.before), gs.read(args.after)
    if before["cases_sha256"] != after["cases_sha256"]:
        raise SystemExit("兩份 run 不是同一份題庫量的")
    lost, gained = regressions(before, after)
    print(f"之前 {json.dumps(before['summary'], ensure_ascii=False)}")
    print(f"之後 {json.dumps(after['summary'], ensure_ascii=False)}")
    print(f"退步 {len(lost)} 題、進步 {len(gained)} 題")
    for case in lost:
        was, now = before["cases"][case], after["cases"][case]
        print(f"  退步 {now['query']}：{was['found']} → {now['found']}")
    if lost:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="guidance_regression")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="由資料產生題庫（唯讀）")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--cases", type=int, default=CASES)
    b.add_argument("--seed", type=int, default=SEED)
    m = sub.add_parser("measure", help="經 gRPC 打服務，記每題的名單與命中")
    m.add_argument("cases", type=Path)
    m.add_argument("--address", default="localhost:50051")
    m.add_argument("--rerank", action="store_true")
    m.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify", help="逐題用規則對資料重算，不同的題就是被改過或手加的")
    v.add_argument("cases", type=Path)
    c = sub.add_parser("compare", help="比兩份 run；有題退步就以 1 結束")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    args = parser.parse_args()
    commands = {"build": cmd_build, "verify": cmd_verify, "measure": cmd_measure}
    commands["compare"] = cmd_compare
    commands[args.command](args)


if __name__ == "__main__":
    main()
