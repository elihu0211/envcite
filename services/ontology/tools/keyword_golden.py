"""關鍵字查詢的黃金題庫：以法規自己的用語定義條為標準答案（唯讀）。

    uv run python tools/keyword_golden.py build  > eval/keyword-golden-<日期>.json
    uv run python tools/keyword_golden.py verify eval/keyword-golden-<日期>.json
    uv run python tools/keyword_golden.py measure eval/keyword-golden-<日期>.json [--address 位址]

既有的黃金題庫是自然語句問題；搜尋頁的使用情境是關鍵字，題庫量不到。這份題庫補它，而且標準答案不來自
被測的檢索系統：

- **用語題（`definition`）**：領域內法規的「本法用詞，定義如下：一、X：指…」，X 是查詢，定義它的
  條文是答案。詞與條文都從原文逐行抽出（`DEFINITION_ITEM`），不靠模型；一個詞在領域內被幾部法規
  定義，這幾條都是答案。領域是專論的「收錄閉包」：既有題庫期望條文所在的法規（種子），加上依它們授權訂定的命令
  （`retrieval.law_scope`）。
- **無答案題（`absent`）**：候選詞經掃描全部現行條文全文，沒有任何一條含有它，標準答案是「沒有」。
- **舊名題（`renamed`）**：沿革記載「原名稱：甲；新名稱：乙」，查詢是舊名甲，答案是改名後的那部法規
  （任一條文都算）。舊名不會出現在現行條文裡，這是「換個說法」的情形，由資料程式抽出、沒有人工維護的
  詞表；舊名也是別部法規的名稱、或任一現行條文含有它切出的全部詞的，不收。

量的是「已知項目搜尋」：標準答案是否出現在前 k 條，以及沒有答案時列表是否仍列出東西。它不量「列出的
其他條文是不是相關」，所以雜訊以「不含查詢詞字面」的命中占比近似，標明是近似。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.settings import ServerSettings, load

#: 標準輸入餵給 `python -`（例如在 pod 裡跑）時沒有 `__file__`，種子改由 `--seeds` 給。
_HERE = globals().get("__file__")
SEED_QRELS = Path(_HERE).resolve().parents[1] / "eval" / "golden-qrels.json" if _HERE else None

#: 定義條：第一行以「定義如下」「用辭之定義如左」「意義如下」「定義及計算公式如下」
#: 這類句子結尾。
#: 只看第一行：「其種類如下」「其類別如下」是分類清單，不是定義。
DEFINING_HEADER = r"(定義|意義|釋義)[^：\n]{0,10}?(如下|如左)[：:]?\s*$"

#: 定義的一行：「一、X：指…」或「（二）X：指…」。X 不含標點，3 至 12 字；括號內是別稱，不進詞。
DEFINITION_ITEM = re.compile(
    r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)"
    r"(?P<term>[^：:、，。；（）()\s]{3,12})(?:（[^）]{1,30}）)?"
    r"：(?P<body>(?:指|謂|係指|是指|稱)\S.*)$"
)

#: 定義一行開頭的編號：「一、」「（二）」。
ITEM_NUMBER = re.compile(r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")

#: 人工逐筆看過、判定不是用語定義的行：它們寫成「X：指…」，但在分類清單裡
#: （「其種類如下」「其類別如下」
#: 「分為下列二種」），不是定義條。獨立完整性核對遇到這些不報，其餘一律報。
REVIEWED_EXCLUSIONS: dict[str, dict[str, str]] = {
    "副產物": {
        "M0040003-art-2": "林產物的分類清單（主產物、副產物）",
        "M0040005-art-3": "林產物的分類清單（主產物、副產物）",
    },
    "地面水體": {"O0040010-art-3": "飲用水種類的分類清單"},
    "地下水體": {"O0040010-art-3": "飲用水種類的分類清單"},
    "生產量": {"J0130001-art-2": "重要物資調查類別的清單"},
}

#: 同一個詞在領域內被這麼多部以上的法規定義，就太泛，不收（答案集太大，已知項目搜尋沒有意義）。
MAX_DEFINERS = 5

#: 無答案題的候選。有些可能出現在某部法規的條文裡，`build` 只留全部現行條文都不含的。
ABSENT_CANDIDATES = (
    "比特幣挖礦",
    "電競選手",
    "寵物美容",
    "珍珠奶茶",
    "太空旅遊",
    "元宇宙",
    "外送平台抽成",
    "直播主",
    "短影音",
    "演唱會搶票",
    "共享單車",
    "自駕計程車",
    "人工生成圖片",
    "月球環境保護",
    "深海採礦許可",
    "火星殖民",
    "電動滑板車回收",
    "寶特瓶押金",
    "氫氣加氣站",
    "人工降雨補償",
    "核廢料海拋",
    "室內裝潢甲醛賠償",
    "冷氣機清洗",
    "民宿冷氣排水",
    "夜市攤販油煙",
    "寺廟燒金紙",
    "鞭炮煙火",
    "露營區垃圾",
    "海灘淨灘",
    "登山健行垃圾",
    "養雞場臭味",
    "養豬場沼液",
    "河川釣魚",
    "賞鳥攝影",
    "颱風假",
    "地震預測",
    "彩券經銷",
    "房屋租金補貼",
    "停車費",
    "機車排氣檢驗站",
)

#: 沿革的更名紀錄：「（原名稱：甲；新名稱：乙）」。
RENAME_PATTERN = r"原名稱[：:]([^；;）)]+)[；;]\s*新名稱"

#: 舊名題每這麼多個舊名取一個（依雜湊，與語料增減無關）。全部舊名有上千個，量測要逐題呼叫。
RENAMED_ONE_IN = 6

#: 規則的預設值，與共用下界檔、平台的覆蓋率門檻相同；`measure` 以參數覆寫。
LEXICAL_FLOOR = 0.5
SEMANTIC_FLOOR = 0.71
MIN_TERM_COVERAGE = 0.5


def seeds(explicit: str | None = None) -> list[str]:
    """既有題庫期望條文所在的法規（上游給定的，不含模型判定）；`explicit` 是逗號分隔的法規代碼。"""
    if explicit:
        return sorted(p for p in explicit.split(",") if p)
    if SEED_QRELS is None:
        raise SystemExit("沒有 __file__ 時要以 --seeds 給種子法規")
    qrels = json.loads(SEED_QRELS.read_text(encoding="utf-8"))
    return sorted({d.split("-art-")[0] for q in qrels["upstream"].values() for d in q})


def split_of(query: str) -> str:
    """依查詢字串雜湊切開發集與測試集，與題庫的增減無關。"""
    return "dev" if hashlib.sha256(query.encode("utf-8")).digest()[0] % 2 == 0 else "test"


def domain_pcodes(conn: psycopg.Connection[Any], as_of: str, seed_list: Sequence[str]) -> set[str]:
    """領域：種子法規加上沿授權（遞迴）訂定、在該日有效的命令，以法規代碼表示。"""
    rows = conn.execute(
        """
        SELECT r.local_id
          FROM unnest(retrieval.law_scope(%(seeds)s::text[], 'zho', %(on)s::date)) AS s(id)
          JOIN corpus.legal_resource AS r ON r.id = s.id
         WHERE r.id_scheme = 'moj_pcode'
        """,
        {"on": as_of, "seeds": list(seed_list)},
    ).fetchall()
    return {str(pcode) for (pcode,) in rows}


def definitions(conn: psycopg.Connection[Any], as_of: str) -> dict[str, list[dict[str, str]]]:
    """全部現行法規定義條裡的每個定義：詞 → 定義它的各條。領域只決定哪些詞成為題目，不限制答案。"""
    rows = conn.execute(
        """
        SELECT r.local_id, e.title, corpus.article_number(p.path), corpus.article_text(v.id)
          FROM corpus.provision AS p
          JOIN corpus.provision_version AS v ON v.provision_id = p.id
                                            AND v.superseded_at IS NULL
                                            AND v.force_period @> %(on)s::date
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                                           AND e.language = 'zho' AND e.text_status = 'available'
          JOIN corpus.legal_resource AS r ON r.id = p.resource_id
         WHERE p.provision_type = 'article'
           AND split_part(corpus.article_text(v.id), E'\n', 1) ~ %(pattern)s
         ORDER BY r.local_id, v.id
        """,
        {"on": as_of, "pattern": DEFINING_HEADER},
    ).fetchall()
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    for pcode, title, number, text in rows:
        for line in str(text).splitlines():
            m = DEFINITION_ITEM.match(line.strip())
            if m:
                found[m["term"]].append(
                    {
                        "article": f"{pcode}-art-{number}",
                        "law": str(title),
                        "number": str(number),
                        "line": line.strip(),
                    }
                )
    return found


def absent_terms(conn: psycopg.Connection[Any], as_of: str) -> tuple[list[str], list[str]]:
    """候選詞裡，全部現行條文全文都不含的（留下），與含有的（丟棄）。"""
    rows = conn.execute(
        """
        SELECT t.term, count(*)
          FROM unnest(%(terms)s::text[]) AS t(term)
          JOIN LATERAL (
              SELECT v.id
                FROM corpus.provision_version AS v
                JOIN corpus.provision AS p ON p.id = v.provision_id AND p.provision_type = 'article'
               WHERE v.superseded_at IS NULL AND v.force_period @> %(on)s::date
                 AND corpus.article_text(v.id) LIKE '%%' || t.term || '%%'
               LIMIT 1
          ) AS hit ON true
         GROUP BY t.term
        """,
        {"terms": list(ABSENT_CANDIDATES), "on": as_of},
    ).fetchall()
    present = {str(term) for term, _ in rows}
    keep = [t for t in ABSENT_CANDIDATES if t not in present]
    return keep, sorted(present)


def renamed_items(
    conn: psycopg.Connection[Any], as_of: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """舊名題與被丟棄的（舊名出現在現行條文裡）：標準答案是改名後的法規。"""
    from envcite_ontology.store import OntologyStore
    from envcite_segmenter import Segmenter

    rows = conn.execute(
        """
        WITH renames AS (
            SELECT r.local_id AS pcode, r.id AS rid, btrim(m[1]) AS old_name
              FROM corpus.lifecycle_event AS e
              JOIN corpus.legal_resource AS r ON r.id = e.resource_id AND r.id_scheme = 'moj_pcode'
             CROSS JOIN LATERAL regexp_matches(e.source_text, %(pattern)s, 'g') AS m
             WHERE e.source_text LIKE '%%原名稱%%'
        ), cur AS (
            SELECT DISTINCT ON (x.resource_id) x.resource_id, x.title
              FROM corpus.legal_expression AS x WHERE x.language = 'zho'
             ORDER BY x.resource_id, x.version_date DESC
        )
        SELECT n.old_name, min(n.pcode), min(c.title)
          FROM renames AS n JOIN cur AS c ON c.resource_id = n.rid
         WHERE n.old_name <> c.title
           AND char_length(n.old_name) BETWEEN 4 AND 30
           AND EXISTS (SELECT 1
                         FROM corpus.provision_version AS v
                         JOIN corpus.provision AS p ON p.id = v.provision_id
                        WHERE p.resource_id = n.rid AND v.superseded_at IS NULL
                          AND v.force_period @> %(on)s::date)
           AND NOT EXISTS (SELECT 1 FROM corpus.legal_expression AS x
                            WHERE x.title = n.old_name AND x.resource_id <> n.rid)
         GROUP BY n.old_name
        HAVING count(DISTINCT n.pcode) = 1
         ORDER BY n.old_name
        """,
        {"pattern": RENAME_PATTERN, "on": as_of},
    ).fetchall()
    chosen = [
        (str(name), str(pcode), str(title))
        for name, pcode, title in rows
        if hashlib.sha256(f"renamed:{name}".encode()).digest()[1] % RENAMED_ONE_IN == 0
    ]
    store = OntologyStore.from_dsn(load(ServerSettings).dsn)
    try:
        segmenter = Segmenter(store.lexicon())
    finally:
        store.close()
    items: list[dict[str, Any]] = []
    literal: list[str] = []
    for name, pcode, title in chosen:
        if terms_in_one_article(conn, segmenter, name, as_of):
            literal.append(name)
            continue
        items.append(
            {
                "id": f"kw-ren-{len(items) + 1:04d}",
                "bucket": "renamed",
                "query": name,
                "split": split_of(name),
                "expected": [{"pcode": pcode, "law": title}],
            }
        )
    return items, literal


def terms_in_one_article(
    conn: psycopg.Connection[Any], segmenter: Any, text: str, as_of: str
) -> bool:
    """現行條文裡是否有一條含有查詢切出的全部詞（字面臂的 AND，比詞組相鄰更寬）。"""
    terms = segmenter.to_tsvector_input(text)
    if not terms:
        return False
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM retrieval.passage AS i
              JOIN corpus.provision_version AS v ON v.content_sha256 = i.content_sha256
             WHERE i.tokenizer = %(tokenizer)s
               AND i.tsv @@ plainto_tsquery('simple', %(terms)s)
               AND v.superseded_at IS NULL
               AND v.force_period @> %(on)s::date)
        """,
        {"tokenizer": segmenter.tokenizer_id, "terms": terms, "on": as_of},
    ).fetchone()
    return bool(row and row[0])


def snapshots(conn: psycopg.Connection[Any]) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT ON (feed) feed, data_date, encode(sha256, 'hex')"
        "  FROM source.snapshot ORDER BY feed, data_date DESC, id DESC"
    ).fetchall()
    return [{"feed": str(f), "data_date": d.isoformat(), "sha256": str(h)} for f, d, h in rows]


def build(conn: psycopg.Connection[Any], as_of: str, seed_list: Sequence[str]) -> dict[str, Any]:
    domain = domain_pcodes(conn, as_of, seed_list)
    found = definitions(conn, as_of)
    items: list[dict[str, Any]] = []
    dropped_generic: list[str] = []
    for term in sorted(found):
        expected = found[term]
        if not any(e["article"].split("-art-")[0] in domain for e in expected):
            continue  # 只在領域外被定義：不是這個領域的題
        if len({e["article"] for e in expected}) > MAX_DEFINERS:
            dropped_generic.append(term)
            continue
        items.append(
            {
                "id": f"kw-def-{len(items) + 1:04d}",
                "bucket": "definition",
                "query": term,
                "split": split_of(term),
                "expected": sorted(
                    {e["article"]: e for e in expected}.values(), key=lambda e: e["article"]
                ),
            }
        )
    keep, present = absent_terms(conn, as_of)
    for term in keep:
        items.append(
            {
                "id": f"kw-abs-{len(items) + 1:04d}",
                "bucket": "absent",
                "query": term,
                "split": split_of(term),
                "expected": [],
            }
        )
    renamed, renamed_literal = renamed_items(conn, as_of)
    for item in renamed:
        item["id"] = f"kw-ren-{len(items) + 1:04d}"
        items.append(item)
    return {
        "kind": "keyword-golden",
        "as_of": as_of,
        "seeds": list(seed_list),
        "domain_laws": len(domain),
        "snapshots": snapshots(conn),
        "dropped_generic_terms": dropped_generic,
        "reviewed_exclusions": REVIEWED_EXCLUSIONS,
        "dropped_absent_candidates": present,
        "dropped_renamed_literal": renamed_literal,
        "items": items,
    }


def verify(conn: psycopg.Connection[Any], doc: Mapping[str, Any], sample: int) -> list[str]:
    """逐題對原文重核：失敗的訊息；空串列表示全部對。印出固定種子的抽樣供人工逐筆看。"""
    problems: list[str] = []
    as_of = str(doc["as_of"])
    rebuilt = build(conn, as_of, doc["seeds"])
    theirs = {i["query"]: i for i in rebuilt["items"]}
    for item in doc["items"]:
        now = theirs.get(item["query"])
        if now is None:
            problems.append(f"{item['id']} {item['query']}：重建時不存在")
        elif answer_ids(now) != answer_ids(item):
            problems.append(f"{item['id']} {item['query']}：答案條文與重建不同")
    for item in doc["items"]:
        if item["bucket"] == "renamed":
            problems.extend(check_renamed(conn, item, as_of))
            continue
        for e in item["expected"]:
            pcode, number = e["article"].split("-art-")
            row = conn.execute(
                """
                SELECT corpus.article_text(v.id)
                  FROM corpus.provision AS p
                  JOIN corpus.provision_version AS v ON v.provision_id = p.id
                                                    AND v.superseded_at IS NULL
                                                    AND v.force_period @> %(on)s::date
                  JOIN corpus.legal_resource AS r ON r.id = p.resource_id
                 WHERE r.local_id = %(pcode)s AND p.provision_type = 'article'
                   AND corpus.article_number(p.path) = %(n)s
                """,
                {"on": as_of, "pcode": pcode, "n": number},
            ).fetchone()
            text = "" if row is None else str(row[0])
            if e["line"] not in text.replace("\r", "").splitlines() and e["line"] not in text:
                problems.append(f"{item['id']} {item['query']}：{e['article']} 的原文沒有這一行")
            if not ITEM_NUMBER.sub("", e["line"]).startswith(item["query"]):
                problems.append(f"{item['id']}：答案行不以查詢詞開頭")
    missed = missed_definitions(conn, doc)
    for term, articles in sorted(missed.items()):
        problems.append(f"「{term}」另有定義行出現在不在答案裡的條文：{', '.join(articles)}")
    rng = random.Random(0)
    picked = rng.sample(doc["items"], min(sample, len(doc["items"])))
    print(f"抽樣 {len(picked)} 題（固定種子，供人工逐筆對照原文）：", file=sys.stderr)
    for item in sorted(picked, key=lambda i: i["id"]):
        if item["bucket"] == "absent":
            print(f"  [{item['id']}] 無答案：「{item['query']}」", file=sys.stderr)
        elif item["bucket"] == "renamed":
            law = item["expected"][0]
            print(f"  [{item['id']}] 舊名「{item['query']}」→ {law['law']}", file=sys.stderr)
        else:
            for e in item["expected"]:
                print(
                    f"  [{item['id']}] 「{item['query']}」→ {e['law']} 第 {e['number']} 條："
                    f"{e['line']}",
                    file=sys.stderr,
                )
    return problems


def answer_ids(item: Mapping[str, Any]) -> list[str]:
    """答案的識別：用語題是條文，舊名題是法規。"""
    return [e.get("article") or e["pcode"] for e in item["expected"]]


def check_renamed(conn: psycopg.Connection[Any], item: Mapping[str, Any], as_of: str) -> list[str]:
    """舊名題對沿革原文核對：更名紀錄確實寫了這個舊名、舊名不是現名。"""
    name = str(item["query"])
    law = item["expected"][0]
    row = conn.execute(
        """
        SELECT count(*)
          FROM corpus.lifecycle_event AS e
          JOIN corpus.legal_resource AS r ON r.id = e.resource_id
         WHERE r.local_id = %(pcode)s
           AND (position(%(a)s IN e.source_text) > 0 OR position(%(b)s IN e.source_text) > 0)
        """,
        {"pcode": law["pcode"], "a": f"原名稱：{name}", "b": f"原名稱:{name}"},
    ).fetchone()
    problems = []
    if not row or not row[0]:
        problems.append(f"{item['id']} 「{name}」：沿革沒有「原名稱：{name}」")
    if name == law["law"]:
        problems.append(f"{item['id']} 「{name}」：舊名等於現名")
    return problems


def missed_definitions(
    conn: psycopg.Connection[Any], doc: Mapping[str, Any]
) -> dict[str, list[str]]:
    """獨立的完整性核對：不要求「用詞，定義如下」的標題句，掃全部現行條文裡長得像定義的行，
    回傳「定義了題庫的某個詞、卻不在該題答案裡」的條文。"""
    wanted = {
        i["query"]: {e["article"] for e in i["expected"]}
        for i in doc["items"]
        if i["bucket"] == "definition"
    }
    rows = conn.execute(
        """
        SELECT r.local_id, corpus.article_number(p.path), corpus.article_text(v.id)
          FROM corpus.provision AS p
          JOIN corpus.provision_version AS v ON v.provision_id = p.id
                                            AND v.superseded_at IS NULL
                                            AND v.force_period @> %(on)s::date
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                                           AND e.language = 'zho' AND e.text_status = 'available'
          JOIN corpus.legal_resource AS r ON r.id = p.resource_id
         WHERE p.provision_type = 'article'
           AND corpus.article_text(v.id) ~ '[、）][^：\n]{3,12}：(指|謂|係指|是指|稱)'
        """,
        {"on": str(doc["as_of"])},
    ).fetchall()
    extras: dict[str, list[str]] = defaultdict(list)
    for pcode, number, text in rows:
        article = f"{pcode}-art-{number}"
        for line in str(text).splitlines():
            m = DEFINITION_ITEM.match(line.strip())
            if (
                m
                and m["term"] in wanted
                and article not in wanted[m["term"]]
                and article not in REVIEWED_EXCLUSIONS.get(m["term"], {})
            ):
                extras[m["term"]].append(article)
    return {term: sorted(set(arts)) for term, arts in extras.items()}


def doc_id(local_id: str, number: str) -> str:
    return f"{local_id}-art-{number}"


def similarity(hit: Any) -> float:
    return float(hit.semantic_similarity) if hit.HasField("semantic_similarity") else 0.0


def rule_variants(args: argparse.Namespace) -> dict[str, Callable[[Any], bool]]:
    """並列比較的列表規則：前兩個是現行行為，其餘是候選（只量、不改系統）。"""
    lexical, semantic, coverage = args.lexical, args.semantic, args.coverage

    def current(hit: Any, theta: float) -> bool:
        return (
            hit.guidance_documents > 0
            or similarity(hit) >= semantic
            or (hit.lexical_score >= lexical and hit.term_coverage >= theta)
        )

    def whole_term(hit: Any) -> bool:
        """條文含全部查詢詞、而且字面臂有命中：不再被字面下界擋下。"""
        return hit.term_coverage >= 1.0 and hit.lexical_score > 0

    def semantic_needs_a_term(hit: Any) -> bool:
        """只靠語意相似度進來、卻完全不含任何查詢詞的命中不列。"""
        return (
            hit.guidance_documents > 0
            or (similarity(hit) >= semantic and hit.term_coverage > 0)
            or (hit.lexical_score >= lexical and hit.term_coverage >= coverage)
        )

    return {
        "consult 的規則（覆蓋率 0）": lambda h: current(h, 0.0),
        "列表規則": lambda h: current(h, coverage),
        "候選 A：整詞命中放行": lambda h: current(h, coverage) or whole_term(h),
        "候選 B：語意命中要沾到字面": semantic_needs_a_term,
        "候選 A＋B": lambda h: semantic_needs_a_term(h) or whole_term(h),
    }


def measure(doc: Mapping[str, Any], address: str, args: argparse.Namespace) -> dict[str, Any]:
    import grpc

    from envcite.ontology.v2 import ontology_pb2 as pb
    from envcite.ontology.v2 import ontology_pb2_grpc as grpc_api

    stub = grpc_api.OntologyServiceStub(grpc.insecure_channel(address))

    def ask(key: tuple[str, bool]) -> Any:
        query, guidance = key
        return stub.SearchArticles(
            pb.SearchArticlesRequest(query=query, limit=10, include_guidance=guidance), timeout=60
        )

    # 全部查詢平行取回：一輪要幾百次呼叫，循序跑得夠久，會碰上 staging 的滾動更新而前後不同版。
    wanted = {
        (i["query"], guided)
        for i in doc["items"]
        if i["split"] in args.splits
        for guided in ((False, True) if i["bucket"] != "absent" else (True,))
    }
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        responses = dict(zip(sorted(wanted), pool.map(ask, sorted(wanted)), strict=True))

    ks = (1, 5, 10)
    out: dict[str, Any] = {}
    for split in args.splits:
        items = [i for i in doc["items"] if i["split"] == split]
        defs = [i for i in items if i["bucket"] == "definition"]
        neg = [i for i in items if i["bucket"] == "absent"]
        ren = [i for i in items if i["bucket"] == "renamed"]
        raw_hit = dict.fromkeys(ks, 0)
        rules = rule_variants(args)
        listed: dict[str, dict[str, float]] = {name: defaultdict(float) for name in rules}
        for item in defs:
            want = {e["article"] for e in item["expected"]}
            raw = responses[(item["query"], False)]
            ranked = [doc_id(h.identifier.local_id, h.number) for h in raw.hits]
            for k in ks:
                raw_hit[k] += bool(want & set(ranked[:k]))
            guided = responses[(item["query"], True)]
            for name, rule in rules.items():
                kept = [h for h in guided.hits if rule(h)]
                ids = [doc_id(h.identifier.local_id, h.number) for h in kept]
                acc = listed[name]
                acc["found"] += bool(want & set(ids))
                acc["size"] += len(kept)
                acc["no_literal"] += sum(item["query"] not in h.content for h in kept)
        for item in neg:
            guided = responses[(item["query"], True)]
            for name, rule in rules.items():
                if any(rule(h) for h in guided.hits):
                    listed[name]["leak"] += 1
        renamed_raw = 0
        renamed_found = dict.fromkeys(rules, 0)
        renamed_size = dict.fromkeys(rules, 0)
        for item in ren:
            pcode = item["expected"][0]["pcode"]
            raw = responses[(item["query"], False)]
            renamed_raw += any(h.identifier.local_id == pcode for h in raw.hits)
            guided = responses[(item["query"], True)]
            for name, rule in rules.items():
                kept = [h for h in guided.hits if rule(h)]
                renamed_found[name] += any(h.identifier.local_id == pcode for h in kept)
                renamed_size[name] += len(kept)
        r = len(ren)
        n, m = len(defs), len(neg)
        out[split] = {
            "renamed_queries": r,
            "renamed_raw_found": renamed_raw / r if r else None,
            "renamed_rules": {
                name: {
                    "law_in_list": renamed_found[name] / r if r else None,
                    "mean_list_size": renamed_size[name] / r if r else None,
                }
                for name in rules
            },
            "definition_queries": n,
            "absent_queries": m,
            "raw_hit_at": {str(k): raw_hit[k] / n for k in ks} if n else {},
            "rules": {
                name: {
                    "found_in_list": v["found"] / n if n else None,
                    "mean_list_size": v["size"] / n if n else None,
                    "listed_without_the_term": (v["no_literal"] / v["size"]) if v["size"] else None,
                    "absent_leak": v["leak"] / m if m else None,
                }
                for name, v in listed.items()
            },
        }
    return {
        "parameters": {
            "lexical": args.lexical,
            "semantic": args.semantic,
            "coverage": args.coverage,
        },
        "results": out,
    }


def markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "| 子集 | 規則 | 答案條在列表 | 平均列出條數 | 列出但不含查詢詞 | 無答案題誤放 |",
        "|---|---|---|---|---|---|",
    ]
    for split, r in result["results"].items():
        for name, v in r["rules"].items():

            def f(x: float | None) -> str:
                return "—" if x is None else f"{x:.3f}"

            lines.append(
                f"| {split}（用語 {r['definition_queries']}、無答案 {r['absent_queries']}）"
                f" | {name} | {f(v['found_in_list'])} | {f(v['mean_list_size'])}"
                f" | {f(v['listed_without_the_term'])} | {f(v['absent_leak'])} |"
            )
    raw = [
        f"{s}: " + "、".join(f"@{k} {v:.3f}" for k, v in r["raw_hit_at"].items())
        for s, r in result["results"].items()
    ]
    lines += [
        "",
        "| 子集 | 規則 | 舊名題：改名後的法規在列表 | 平均列出條數 |",
        "|---|---|---|---|",
    ]
    for split, r in result["results"].items():
        for name, v in r.get("renamed_rules", {}).items():
            found, size = v["law_in_list"], v["mean_list_size"]
            cells = ["—" if x is None else f"{x:.3f}" for x in (found, size)]
            label = f"{split}（舊名 {r['renamed_queries']}）"
            lines.append(f"| {label} | {name} | {cells[0]} | {cells[1]} |")
    raw += [
        f"{s} 舊名題，未過濾的前 10 條含改名後的法規："
        + ("—" if r.get("renamed_raw_found") is None else f"{r['renamed_raw_found']:.3f}")
        for s, r in result["results"].items()
    ]
    return "\n".join(lines) + "\n\n未過濾的前 k 條含答案條的比例：" + "；".join(raw) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--as-of", default=date.today().isoformat())
    b.add_argument("--seeds", help="逗號分隔的種子法規代碼；預設取既有題庫的期望條文所在的法規")
    v = sub.add_parser("verify")
    v.add_argument("golden", type=Path)
    v.add_argument("--sample", type=int, default=30)
    m = sub.add_parser("measure")
    m.add_argument("golden", type=Path)
    m.add_argument("--address", default="localhost:50051")
    m.add_argument(
        "--workers",
        type=int,
        default=1,
        help="平行呼叫數；不得超過服務的嵌入名額（2），否則語意臂被略過、數字退化成字面臂",
    )
    m.add_argument("--lexical", type=float, default=LEXICAL_FLOOR)
    m.add_argument("--semantic", type=float, default=SEMANTIC_FLOOR)
    m.add_argument("--coverage", type=float, default=MIN_TERM_COVERAGE)
    m.add_argument(
        "--splits",
        nargs="+",
        choices=("dev", "test"),
        default=["dev"],
        help="預設只量開發集；測試集在規則定案後只報一次",
    )
    args = p.parse_args(argv)

    if args.command == "measure":
        doc = json.loads(args.golden.read_text(encoding="utf-8"))
        result = measure(doc, args.address, args)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        print(markdown(result), file=sys.stderr)
        return 0

    with psycopg.connect(load(ServerSettings).dsn) as conn:
        conn.read_only = True
        if args.command == "build":
            doc = build(conn, args.as_of, seeds(args.seeds))
            print(json.dumps(doc, ensure_ascii=False, indent=1))
            by: defaultdict[tuple[str, str], int] = defaultdict(int)
            for i in doc["items"]:
                by[(i["bucket"], i["split"])] += 1
            print(
                f"題數：{dict(by)}；丟棄過泛的詞 {len(doc['dropped_generic_terms'])}、"
                f"丟棄含詞的無答案候選 {doc['dropped_absent_candidates']}；"
                f"舊名因出現在條文而丟棄 {len(doc['dropped_renamed_literal'])}",
                file=sys.stderr,
            )
            return 0
        doc = json.loads(args.golden.read_text(encoding="utf-8"))
        problems = verify(conn, doc, args.sample)
        for line in problems:
            print("不符：" + line, file=sys.stderr)
        print(f"核對 {len(doc['items'])} 題，不符 {len(problems)}", file=sys.stderr)
        return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
