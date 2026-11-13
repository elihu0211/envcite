"""原生黃金題庫：由語料自己的條文結構產生，標準答案不來自被測的檢索系統（唯讀）。

    uv run python tools/native_golden.py build  --out eval/native-golden-<日期>.json
    uv run python tools/native_golden.py verify eval/native-golden-<日期>.json
    uv run python tools/native_golden.py derive eval/native-golden-<日期>.json

題庫自成一格：條文以語料的法規代碼加條號為鍵（`<pcode>-art-<n>`），不依任何外部題庫、法規名稱對照或
領域種子；題目與答案都由條文原文的結構程式化產生，
**不用模型出題**（模型出的題會把「系統的輸出變成標準
答案」的循環帶回來，也容易與條文字面相近而高估字面臂）。每題記它的產生規則與原文證據，
要查證只需對照條文。

題型（`kind`）對應既有量測流程的桶（`bucket`）：

- `definition_term`、`definition_question`（exact_id）：法規定義條裡「X：指…」的 X，
  查詢是詞本身或「X的定義是什麼」，
  答案是定義它的所有現行條文。
- `duty_penalty`（multi_hop）：罰則條寫「違反第N條規定者，處…」，
  問「違反本法第N條會受什麼處罰」，答案是罰則條，
  要沿著「違反第N條」這條引用才找得到。
- `delegation`（multi_hop）：命令第一條寫「本辦法依○○法第N條…規定訂定」，
  問「這部命令依哪一條訂定」，答案是
  母法的授權條文。
- `applies_mutatis_mutandis`（multi_hop）：條文寫「準用第M條之規定」，問「這一條準用哪一條」，
  答案是被準用的條文。
- `absent_term`、`absent_question`（negative）：候選詞掃過全部現行條文全文都不含，
  標準答案是「沒有」，量拒答。

沒有 paraphrase 桶：同義改寫需要模型，沒有獨立於被測系統的來源前不做。
這份題庫量的是條文結構能問出來的
已知項目與跨條引用，不代表使用者問句的全貌（模板題的句式比真實問句整齊）。

`derive` 產生既有量測流程要的兩份文件：qrels（`<題庫>.qrels.json`）與開發集／測試集切分
（`<題庫>.split.json`）；切分依題的分組鍵（詞、條文）雜湊，同一個詞或條文的題一定在同一邊，
不洩漏到另一邊。
之後用 `golden_retrieval_eval.py measure <題庫> --qrels … --split … --subset dev` 量測，
題庫的 sha256
是檔案位元組的雜湊，改任何一題都會使 qrels 與切分失效、要重新 `derive`。"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs

from envcite_ontology.settings import ServerSettings, load

SCHEMA = "envcite-native-golden/1"
#: 改任何產生規則的輸出時遞增；題庫記著它，重建結果不同時看得出是規則變了還是語料變了。
GENERATOR_VERSION = 1

#: 每種題型最多取幾組（詞、條文、命令）；依分組鍵的雜湊排序後取前面，與題庫增減無關。
CAP = {
    "definition": 300,
    "duty_penalty": 300,
    "delegation": 250,
    "applies_mutatis_mutandis": 150,
}
#: 一個詞在這麼多條以上被定義就太泛（答案集太大，已知項目搜尋沒有意義）。
MAX_DEFINERS = 5
#: 一個條文被這麼多條以上的罰則條引用就不出題（答案集太大）。
MAX_PENALTY_ARTICLES = 4

NUM = r"[0-9〇零一二三四五六七八九十百千]+"
ARTICLE_REF = re.compile(rf"第({NUM})條(?:之({NUM}))?")
#: 「違反」到「規定」之間只准出現這些字：本法自己的引用鏈。
#: 出現法規名稱就整句不收（是別部法規的條文）。
_REF_CHARS = frozenset(
    "第條之項款、或及與前後段但書本法例辦規則標準細準0123456789０１２３４５６７８９〇零一二三四五六七八九十百千"
)
_VIOLATION = re.compile(r"違反([^，。；：\n]{1,80}?)規定")
_PENALTY_AFTER = re.compile(r"[^。；\n]{0,40}?[處科罰]")
_SELF = ("本法", "本條例")
_DIGITS = {c: i for i, c in enumerate("〇一二三四五六七八九")} | {"零": 0}
_UNITS = {"十": 10, "百": 100, "千": 1000}

DEFINING_HEADER = re.compile(r"(定義|意義|釋義)[^：\n]{0,10}?(如下|如左)[：:]?\s*$")
ITEM_NUMBER = re.compile(r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)")
DEFINITION_ITEM = re.compile(
    r"^(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)"
    r"(?P<term>[^：:、，。；（）()\s]{3,12})(?:（[^）]{1,30}）)?"
    r"：(?P<body>(?:指|謂|係指|是指|稱)\S.*)$"
)
#: 命令第一條：「本辦法依○○法（以下簡稱本法）第N條第M項及第K條規定訂定之」。
_DELEGATION = re.compile(
    rf"依據?(?P<law>[^（()第，。；、依據]{{3,30}}?)(?:（[^）]*）)?"
    rf"(?P<refs>第{NUM}條(?:之{NUM})?(?:(?:第{NUM}項|第{NUM}款|前段|後段|但書)|(?:、|及|或|與)第{NUM}條(?:之{NUM})?)*)"
    r"(?:之)?規定"
)
_APPLIES = re.compile(
    rf"準用(?:本法|本條例)?(?P<refs>第{NUM}條(?:之{NUM})?(?:第{NUM}項|第{NUM}款)?"
    rf"(?:(?:、|及|或|與)第{NUM}條(?:之{NUM})?(?:第{NUM}項|第{NUM}款)?)*)(?:之)?規定"
)

#: 無答案題的候選詞：日常生活與新興事物，沒有法規條文會寫到的詞；
#: `build` 只留掃過全部現行條文都不含的。
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
    "火星殖民",
    "電動滑板車回收",
    "寶特瓶押金",
    "人工降雨補償",
    "室內裝潢甲醛賠償",
    "冷氣機清洗",
    "夜市攤販油煙",
    "寺廟燒金紙",
    "露營區垃圾",
    "登山健行垃圾",
    "養雞場臭味",
    "河川釣魚",
    "賞鳥攝影",
    "颱風假",
    "地震預測",
    "停車費",
    "房屋租金補貼",
    "機車排氣檢驗站",
    "網紅帶貨",
    "訂閱制影音平台",
    "手搖飲料杯",
    "密室逃脫",
    "掃地機器人",
    "智慧音箱",
    "無人機表演",
    "咖啡廳寵物",
    "社區團購",
    "共享充電寶",
    "加密貨幣交易所",
    "虛擬偶像",
    "電子競技場館",
    "寵物旅館",
    "親子餐廳",
    "烘焙工作室",
    "瑜伽教室",
    "自助洗衣店",
    "兒童樂園",
    "漫畫出租店",
    "網咖",
    "演唱會周邊商品",
    "雙十一購物節",
    "年終獎金課稅",
    "線上遊戲虛寶",
    "二手車評價",
    "婚禮顧問",
)


@dataclass(frozen=True)
class Article:
    pcode: str
    title: str
    number: str
    text: str
    norm_kind: str

    @property
    def key(self) -> str:
        return f"{self.pcode}-art-{self.number}"


# ── 純函式：條文文字 → 結構 ─────────────────────────────────────────────


def zh_to_int(text: str) -> int | None:
    """「三十一」→ 31；「一百零五」→ 105；「十」→ 10；阿拉伯數字照讀；讀不懂回 None。"""
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if text.isdigit():
        return int(text)
    if not text or any(c not in _DIGITS and c not in _UNITS for c in text):
        return None
    if all(c in _DIGITS for c in text):  # 「二〇一」：逐位
        return int("".join(str(_DIGITS[c]) for c in text))
    total, number = 0, None
    for c in text:
        if c in _DIGITS:
            number = _DIGITS[c]
        else:
            total += (1 if number is None else number) * _UNITS[c]
            number = None
    return total + (number or 0)


def article_number(main: str, sub: str | None) -> str | None:
    """「三十一」「一」→ `31-1`；與語料的 `corpus.article_number` 同形。"""
    n = zh_to_int(main)
    if n is None:
        return None
    if sub is None:
        return str(n)
    s = zh_to_int(sub)
    return None if s is None else f"{n}-{s}"


def refs_of(text: str) -> list[str]:
    """文字裡依序出現的條號（去重、保序）；讀不懂的略過。"""
    seen: dict[str, None] = {}
    for m in ARTICLE_REF.finditer(text):
        n = article_number(m[1], m[2])
        if n is not None:
            seen.setdefault(n)
    return list(seen)


def violation_refs(text: str) -> list[tuple[str, list[str]]]:
    """罰則句：「違反（本法）第N條…規定者，處…」→ [(那一句, [N…])]。

    只收「違反」與「規定」之間完全是本法自己的條文引用的句子：出現法規名稱（別部法規的條文）、
    範圍（至）或讀不懂的條號就整句不收，寧缺勿濫，所以這不是罰則條的完整清單
    （見 `verify` 的獨立核對）。
    """
    found: list[tuple[str, list[str]]] = []
    for m in _VIOLATION.finditer(text):
        clause = m[1]
        if not set(clause) <= _REF_CHARS:
            continue
        if "本" in clause and not (clause.startswith(_SELF) and clause.count("本") == 1):
            continue  # 「本辦法」「本規則」在法律裡不是它自己，別處的「本」也不是
        refs = refs_of(clause)
        if not refs or not _PENALTY_AFTER.match(text, m.end()):
            continue
        found.append((m[0], refs))
    return found


def definitions_of(article: Article) -> list[tuple[str, str]]:
    """定義條的每個定義：[(詞, 那一行)]。只認第一行以「定義如下」之類結尾的條文。"""
    lines = article.text.splitlines()
    if not lines or not DEFINING_HEADER.search(lines[0].strip()):
        return []
    out = []
    for line in lines:
        m = DEFINITION_ITEM.match(line.strip())
        if m:
            out.append((m["term"], line.strip()))
    return out


# ── 語料 ───────────────────────────────────────────────────────────────


def load_articles(conn: psycopg.Connection[Any], as_of: str) -> dict[str, Article]:
    """`as_of` 當日有效、全文可得的中央法規條文，以條文代號為鍵。"""
    rows = conn.execute(
        """
        SELECT r.local_id, e.title, corpus.article_number(p.path), corpus.article_text(v.id),
               r.norm_kind::text
          FROM corpus.provision AS p
          JOIN corpus.provision_version AS v ON v.provision_id = p.id
                                            AND v.superseded_at IS NULL
                                            AND v.force_period @> %(on)s::date
          JOIN corpus.legal_expression AS e ON e.id = v.expression_id
                                           AND e.language = 'zho' AND e.text_status = 'available'
          JOIN corpus.legal_resource AS r ON r.id = p.resource_id
         WHERE p.provision_type = 'article'
           AND r.jurisdiction = 'TW-CENTRAL' AND r.id_scheme = 'moj_pcode'
         ORDER BY r.local_id, v.id
        """,
        {"on": as_of},
    ).fetchall()
    articles: dict[str, Article] = {}
    for pcode, title, number, text, kind in rows:
        a = Article(str(pcode), str(title), str(number), str(text).replace("\r", ""), str(kind))
        articles.setdefault(a.key, a)
    return articles


def snapshots(conn: psycopg.Connection[Any]) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT ON (feed) feed, data_date, encode(sha256, 'hex')"
        "  FROM source.snapshot ORDER BY feed, data_date DESC, id DESC"
    ).fetchall()
    return [{"feed": str(f), "data_date": d.isoformat(), "sha256": str(h)} for f, d, h in rows]


# ── 產生器 ─────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _pick(groups: Mapping[str, Any], cap: int) -> list[str]:
    """依分組鍵的雜湊排序取前 `cap` 組：與題庫的增減、語料的順序無關。"""
    return sorted(groups, key=lambda k: _digest("pick", k))[:cap]


def _expected(
    articles: Mapping[str, Article], keys: Iterable[str], evidence: Mapping[str, str]
) -> list[dict[str, str]]:
    return [
        {
            "article": k,
            "law": articles[k].title,
            "number": articles[k].number,
            "evidence": evidence.get(k, ""),
        }
        for k in sorted(set(keys))
    ]


def _item(
    kind: str, bucket: str, group: str, question: str, expected: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "id": f"{kind}-{_digest(kind, group, question)[:10]}",
        "bucket": bucket,
        "kind": kind,
        "group": group,
        "question": question,
        "expected": expected,
    }


def gen_definitions(articles: Mapping[str, Article]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, str]] = defaultdict(dict)
    for a in articles.values():
        for term, line in definitions_of(a):
            found[term].setdefault(a.key, line)
    usable = {t: e for t, e in found.items() if len(e) <= MAX_DEFINERS}
    items = []
    for term in _pick(usable, CAP["definition"]):
        expected = _expected(articles, usable[term], usable[term])
        items.append(_item("definition_term", "exact_id", term, term, expected))
        items.append(
            _item("definition_question", "exact_id", term, f"{term}的定義是什麼？", expected)
        )
    return items


def gen_duty_penalty(articles: Mapping[str, Article]) -> list[dict[str, Any]]:
    """（法規，被違反的條）→ 罰則條。只用法律（statute）：命令裡的「本法」指母法，不是它自己。"""
    penalties: dict[str, dict[str, str]] = defaultdict(dict)
    for a in articles.values():
        if a.norm_kind != "statute":
            continue
        for sentence, refs in violation_refs(a.text):
            for n in refs:
                duty = f"{a.pcode}-art-{n}"
                if duty in articles and duty != a.key:
                    penalties[duty].setdefault(a.key, sentence)
    usable = {d: p for d, p in penalties.items() if len(p) <= MAX_PENALTY_ARTICLES}
    items = []
    for duty in _pick(usable, CAP["duty_penalty"]):
        a = articles[duty]
        question = f"違反{a.title}第{a.number}條規定，會受到什麼處罰？"
        items.append(
            _item(
                "duty_penalty",
                "multi_hop",
                duty,
                question,
                _expected(articles, usable[duty], usable[duty]),
            )
        )
    return items


def gen_delegation(articles: Mapping[str, Article]) -> list[dict[str, Any]]:
    """命令第一條的訂定依據 → 母法的授權條文。命令名稱重複、母法名稱對不到唯一一部法律的不收。"""
    statutes: dict[str, list[str]] = defaultdict(list)
    orders: dict[str, list[Article]] = defaultdict(list)
    for a in articles.values():
        if a.number == "1" and a.norm_kind == "regulation":
            orders[a.title].append(a)
        if a.norm_kind == "statute":
            statutes[a.title].append(a.pcode)
    candidates: dict[str, tuple[Article, list[str], str]] = {}
    for group in orders.values():
        if len(group) != 1:
            continue
        a = group[0]
        m = _DELEGATION.search(a.text.replace("\n", ""))
        if not m or len(set(statutes.get(m["law"], []))) != 1:
            continue
        pcode = statutes[m["law"]][0]
        keys = [f"{pcode}-art-{n}" for n in refs_of(m["refs"])]
        if keys and all(k in articles for k in keys):
            candidates[a.key] = (a, keys, m[0])
    items = []
    for key in _pick(candidates, CAP["delegation"]):
        a, keys, evidence = candidates[key]
        question = f"「{a.title}」是依哪一部法律的哪一條訂定的？"
        items.append(
            _item(
                "delegation",
                "multi_hop",
                key,
                question,
                _expected(articles, keys, {k: evidence for k in keys}),
            )
        )
    return items


def gen_applies(articles: Mapping[str, Article]) -> list[dict[str, Any]]:
    """法律條文裡的「準用第M條之規定」→ 被準用的條。範圍（至）與跨法規的準用不收。"""
    candidates: dict[str, tuple[Article, list[str], str]] = {}
    for a in articles.values():
        if a.norm_kind != "statute":
            continue
        m = _APPLIES.search(a.text.replace("\n", ""))
        if not m:
            continue
        keys = [f"{a.pcode}-art-{n}" for n in refs_of(m["refs"])]
        if keys and len(keys) <= 3 and a.key not in keys and all(k in articles for k in keys):
            candidates[a.key] = (a, keys, m[0])
    items = []
    for key in _pick(candidates, CAP["applies_mutatis_mutandis"]):
        a, keys, evidence = candidates[key]
        which = "哪一條" if len(keys) == 1 else "哪些條文"
        question = f"{a.title}第{a.number}條準用了{which}的規定？"
        items.append(
            _item(
                "applies_mutatis_mutandis",
                "multi_hop",
                key,
                question,
                _expected(articles, keys, {k: evidence for k in keys}),
            )
        )
    return items


def absent_terms(articles: Mapping[str, Article]) -> tuple[list[str], list[str]]:
    """候選詞裡，全部現行條文全文都不含的（留下），與含有的（丟棄）。"""
    corpus = "\n".join(a.text for a in articles.values())
    keep = [t for t in ABSENT_CANDIDATES if t not in corpus]
    return keep, [t for t in ABSENT_CANDIDATES if t in corpus]


def gen_absent(articles: Mapping[str, Article]) -> list[dict[str, Any]]:
    keep, _ = absent_terms(articles)
    items = []
    for term in keep:
        items.append(_item("absent_term", "negative", term, term, []))
        items.append(_item("absent_question", "negative", term, f"{term}有什麼法律規定？", []))
    return items


GENERATORS = (gen_definitions, gen_duty_penalty, gen_delegation, gen_applies, gen_absent)


def split_of(group: str) -> str:
    """依分組鍵的雜湊切開發集與測試集：同一個詞、條文的題在同一邊，與題庫的增減無關。"""
    return "dev" if hashlib.sha256(group.encode("utf-8")).digest()[0] % 2 == 0 else "test"


def build(conn: psycopg.Connection[Any], as_of: str) -> dict[str, Any]:
    articles = load_articles(conn, as_of)
    items = [i for generate in GENERATORS for i in generate(articles)]
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise SystemExit("題號重複：同一組的題句相同")
    for i in items:
        i["split"] = split_of(i["group"])
    _, present = absent_terms(articles)
    return {
        "schema": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "as_of": as_of,
        "snapshots": snapshots(conn),
        "articles": len(articles),
        "dropped_absent_candidates": present,
        "counts": {
            k: sum(i["kind"] == k for i in items) for k in sorted({i["kind"] for i in items})
        },
        "items": sorted(items, key=lambda i: i["id"]),
    }


# ── 查證 ───────────────────────────────────────────────────────────────


def verify(doc: Mapping[str, Any], articles: Mapping[str, Article], sample: int) -> list[str]:
    """逐題對原文重核：失敗的訊息；空串列表示全部對。印出固定種子的抽樣供人工逐筆看。"""
    problems: list[str] = []
    items: Sequence[Mapping[str, Any]] = doc["items"]
    for item in items:
        if item["split"] != split_of(item["group"]):
            problems.append(f"{item['id']}：切分與分組鍵的雜湊不符")
        if item["bucket"] == "negative":
            term = item["group"]
            if item["expected"] or any(term in a.text for a in articles.values()):
                problems.append(f"{item['id']} 「{term}」：無答案題的詞出現在現行條文，或帶了答案")
            continue
        if not item["expected"]:
            problems.append(f"{item['id']}：可答題沒有答案條文")
        for e in item["expected"]:
            a = articles.get(e["article"])
            if a is None:
                problems.append(f"{item['id']}：{e['article']} 不在現行語料")
            elif (
                e["evidence"]
                and e["evidence"].replace("\n", "") not in a.text.replace("\n", "")
                and not _evidence_elsewhere(item, articles)
            ):
                problems.append(f"{item['id']}：{e['article']} 的原文沒有證據句")
    rebuilt = {i["id"]: i for g in GENERATORS for i in g(articles)}
    mine = {i["id"] for i in items}
    for missing in sorted(mine - rebuilt.keys()):
        problems.append(f"{missing}：重建時不存在")
    for extra in sorted(rebuilt.keys() - mine):
        problems.append(f"{extra}：重建多出這一題（題庫過期或規則改了）")
    problems += _completeness(items, articles)
    rng = random.Random(0)
    picked = rng.sample(list(items), min(sample, len(items)))
    print(f"抽樣 {len(picked)} 題（固定種子，供人工逐筆對照原文）：", file=sys.stderr)
    for item in sorted(picked, key=lambda i: i["id"]):
        print(f"  [{item['id']}] {item['question']}", file=sys.stderr)
        for e in item["expected"]:
            print(f"      → {e['law']} 第 {e['number']} 條：{e['evidence'][:70]}", file=sys.stderr)
    return problems


def _evidence_elsewhere(item: Mapping[str, Any], articles: Mapping[str, Article]) -> bool:
    """授權與準用題的證據句在題目的來源條文（命令第一條、寫著準用的那一條），不在答案條文自己的原文裡：
    此時改查來源條文。"""
    group = str(item["group"])
    if item["kind"] in ("delegation", "applies_mutatis_mutandis"):
        source = articles.get(group)
        return source is not None and all(
            e["evidence"].replace("\n", "") in source.text.replace("\n", "")
            for e in item["expected"]
        )
    return False


def _completeness(items: Sequence[Mapping[str, Any]], articles: Mapping[str, Article]) -> list[str]:
    """獨立的完整性核對：不用產生器的文法，只找「違反…第N條…處」這樣的句子，
    看有沒有罰則條引用了題庫的某個條、卻不在該題答案裡。"""
    wanted = {
        i["group"]: {e["article"] for e in i["expected"]}
        for i in items
        if i["kind"] == "duty_penalty"
    }
    loose = re.compile(
        rf"違反[^。；\n]{{0,30}}?第({NUM})條(?:之({NUM}))?[^。；\n]{{0,60}}?[處科罰]"
    )
    problems = []
    for a in articles.values():
        if a.norm_kind != "statute":
            continue
        for m in loose.finditer(a.text.replace("\n", "")):
            n = article_number(m[1], m[2])
            duty = f"{a.pcode}-art-{n}"
            if duty in wanted and a.key not in wanted[duty] and a.key != duty:
                head = m[0][: m.start(1) - m.start(0)]
                if "本" in head or head == "違反":
                    problems.append(f"{duty}：罰則條 {a.key} 引用它，卻不在答案裡")
    return problems


# ── qrels 與切分 ────────────────────────────────────────────────────────


def derive(path: Path, doc: Mapping[str, Any]) -> tuple[Path, Path]:
    """題庫 → 既有量測流程要的 qrels 與切分文件，寫在題庫旁邊。"""
    golden_sha = gs.file_sha256(path)
    items: Sequence[Mapping[str, Any]] = doc["items"]
    qrels: gs.Qrels = {}
    for i in items:
        if i["bucket"] == gs.NEGATIVE:
            continue
        for e in i["expected"]:
            gs.parse_doc_key(e["article"])  # 條文代號的格式不合就在這裡停
        qrels[i["id"]] = {e["article"]: gs.UPSTREAM_GRADE for e in i["expected"]}
    qrels_path = path.with_suffix(".qrels.json")
    gs.write(
        qrels_path,
        {
            "golden_sha256": golden_sha,
            "snapshots": doc["snapshots"],
            "rule": "原生題庫：期望條文就是條文代號（法規代碼加條號），沒有名稱對照；"
            "題庫給定的條分數為 "
            f"{gs.UPSTREAM_GRADE}，模型判定只能往上調",
            "scale": list(gs.SCALE),
            "relevant_from": gs.RELEVANT_FROM,
            "counts": {
                "items": len(items),
                "scored": len(qrels),
                "unmapped": 0,
                "negative": sum(i["bucket"] == gs.NEGATIVE for i in items),
            },
            "qrels": qrels,
            "upstream": {q: sorted(ds) for q, ds in qrels.items()},
            "graded_by": {},
            "unmapped": {},
            "judgements": [],
        },
    )
    parts: dict[str, list[str]] = {"dev": [], "test": []}
    for i in items:
        parts[i["split"]].append(i["id"])
    parts = {k: sorted(v) for k, v in parts.items()}
    bucket = {i["id"]: i["bucket"] for i in items}
    split_path = path.with_suffix(".split.json")
    gs.write(
        split_path,
        {
            "golden_sha256": golden_sha,
            "seed": "group-hash",
            "test_fraction": None,
            "method": "題的分組鍵（詞、條文）的 sha256 第一個位元組為偶數進 dev、奇數進 test；"
            "同一組的題在同一邊，與題庫的增減無關",
            "counts": {
                name: {b: sum(bucket[q] == b for q in ids) for b in sorted(set(bucket.values()))}
                for name, ids in parts.items()
            },
            **parts,
        },
    )
    return qrels_path, split_path


# ── 指令 ───────────────────────────────────────────────────────────────


def _connect() -> psycopg.Connection[Any]:
    conn = psycopg.connect(load(ServerSettings).dsn)
    conn.read_only = True
    return conn


def _write(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def cmd_build(args: argparse.Namespace) -> None:
    with _connect() as conn:
        doc = build(conn, args.as_of)
    _write(args.out, doc)
    print(f"{doc['counts']}；{args.out}：sha256 {gs.file_sha256(args.out)}")


def cmd_verify(args: argparse.Namespace) -> None:
    doc = json.loads(args.golden.read_text(encoding="utf-8"))
    with _connect() as conn:
        articles = load_articles(conn, str(doc["as_of"]))
    problems = verify(doc, articles, args.sample)
    for p in problems:
        print(f"不符：{p}", file=sys.stderr)
    print(f"{len(doc['items'])} 題，不符 {len(problems)}")
    if problems:
        raise SystemExit(1)


def cmd_derive(args: argparse.Namespace) -> None:
    doc = json.loads(args.golden.read_text(encoding="utf-8"))
    qrels_path, split_path = derive(args.golden, doc)
    print(f"{qrels_path}\n{split_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="native_golden")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="由語料的條文結構產生題庫")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--as-of", default=date.today().isoformat(), help="取哪一天有效的條文")
    v = sub.add_parser("verify", help="對語料逐題重核，另做獨立的完整性核對")
    v.add_argument("golden", type=Path)
    v.add_argument("--sample", type=int, default=40, help="印出幾題固定種子的抽樣供人工對照")
    d = sub.add_parser("derive", help="題庫 → qrels 與開發集／測試集切分")
    d.add_argument("golden", type=Path)
    args = parser.parse_args()
    {"build": cmd_build, "verify": cmd_verify, "derive": cmd_derive}[args.command](args)


if __name__ == "__main__":
    main()
