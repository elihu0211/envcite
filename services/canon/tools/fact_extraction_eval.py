"""量測問答接入推論的第一步：模型能否把問句裡明說的事實對到概念代碼，而且不憑空填值。

全程沒有人工標籤與人工審查：
- 標準答案由已確認陳述的要件結構用程式產生（旗標概念為真，數值概念取滿足比較的值）；
- 問句由與抽取者不同家族的模型，依標準答案改寫成口語；
- 抽取結果以程式比對，並以程式檢查（概念在候選集、原文片段在問句裡、數字在問句裡）。

量三件事：抽取的精確率與召回、「憑空填值」（原文片段不在問句裡的事實，必須為 0），以及把抽出的
事實交給推理機的結果與標準答案交給它的結果是否相同；另量沒有案件事實的問句（對照組）若照常
推論會得到什麼，供「偵測到事實才推論」與「一律推論」兩種閘門的取捨。

用法（從 services/canon 執行）::

    ENVCITE_CANON_DSN=... uv run python tools/fact_extraction_eval.py \
        --out eval/results/fact-extraction-<日期>
"""

import argparse
import hashlib
import json
import math
import random
import re
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple

import psycopg

from canon.db import Conn, fetch
from canon.embedding import Embeds
from canon.reasoning.engine import evaluate
from canon.settings import DatabaseSettings, load
from canon.verification.config import load_declared, load_embedder
from canon.verification.models import ChatCompletionsModel, ModelError

type Kind = Literal["flag", "number"]
type Value = bool | float

#: 預設的抽取模型。抽取要夠強：小模型逐概念判斷會過度解讀，要靠另一個模型逐項過濾才擋得住；
#: 較強的模型不過濾也勝過小模型加過濾（量測結果見 eval/results/fact-extraction-honest-2026-11-18）。
EXTRACTOR_ID = "ornith-35b"
#: 出題者與稽核者；家族都必須不同於抽取者（出題者不得與抽取者同家族，稽核者不得驗自己）。
#: 稽核者有兩位、屬不同家族：單一稽核者誤判多（逐條看約一半），兩位都判沒明說才接近真的過度解讀。
ASKER_ID = "muse-spark-open"
AUDITOR_IDS = (ASKER_ID, "nemotron-3.5-super-vl")

_CONTROL_TOPICS = (
    "立法目的",
    "主管機關是誰",
    "名詞定義",
    "法規的適用範圍",
    "法條的修正沿革",
    "罰則一般而言的種類",
    "申請許可的一般流程",
    "放流水標準是什麼",
    "水污染防治法與其他法律的關係",
    "飲用水水質標準由誰訂定",
)


@dataclass(frozen=True)
class Concept:
    """候選概念：抽取者唯一能選的詞彙。"""

    code: str
    name: str
    kind: Kind
    unit: str | None


class _Cond(NamedTuple):
    statement_id: int
    code: str
    name: str
    comparator: str
    threshold: float | None
    unit: str | None
    text: str


_CONDITIONS = """
WITH conf AS (
  SELECT s.id FROM (SELECT DISTINCT p.resource_id AS id FROM annotation.statement AS x
                      JOIN corpus.provision AS p ON p.id = x.provision_id) AS lr
   CROSS JOIN LATERAL (
         SELECT * FROM annotation.statement_versions_at(lr.id, 'zho', %s) OFFSET 0) AS r
    JOIN annotation.statement AS s ON s.id = r.statement_id)
SELECT c.statement_id,
       first_value(k.code) OVER (PARTITION BY k.scheme_id, k.name ORDER BY k.id), k.name,
       c.comparator::text, c.threshold::float8, c.measure_unit,
       pv.content
  FROM annotation.statement_condition AS c
  JOIN conf ON conf.id = c.statement_id
  JOIN annotation.statement AS st ON st.id = c.statement_id
  JOIN corpus.provision_version AS pv
    ON pv.provision_id = st.provision_id AND pv.superseded_at IS NULL
   AND pv.language = 'zho' AND pv.force_period @> %s::date
  JOIN vocab.concept AS k ON k.id = c.concept_id
 WHERE NOT c.needs_assessment AND NOT c.carried AND c.threshold IS NOT NULL
 ORDER BY c.statement_id, c.id
"""


def conditions(conn: Conn, as_of: date) -> list[_Cond]:
    """某日有效且已確認的陳述的可陳述要件（不含需判斷、承自上位、委由他處的）。"""
    return fetch(conn, _CONDITIONS, (as_of, as_of), _Cond)


def vocabulary(conds: list[_Cond]) -> dict[str, Concept]:
    """候選概念；比較值為 1 的相等比較視為旗標，其餘為數值。"""
    out: dict[str, Concept] = {}
    for c in conds:
        flag = c.comparator == "eq" and c.threshold == 1.0 and c.unit is None
        out.setdefault(c.code, Concept(c.code, c.name, "flag" if flag else "number", c.unit))
    return out


def satisfying(c: _Cond) -> Value:
    """滿足該要件的值：旗標為真，數值取越過門檻的值。"""
    assert c.threshold is not None
    if c.comparator in ("ge", "gt"):
        return float(round(c.threshold * 1.5 + 1))
    if c.comparator in ("le", "lt"):
        return float(max(0, round(c.threshold * 0.5)))
    return True if c.threshold == 1.0 and c.unit is None else c.threshold


@dataclass(frozen=True)
class Item:
    """一題：標準答案與變體。"""

    item_id: str
    variant: str
    gold: dict[str, Value]
    context: tuple[str, ...] = ()


def build_items(
    conds: list[_Cond], vocab: dict[str, Concept], seed: int, controls: int
) -> list[Item]:
    """由陳述的要件結構產生題目：完整、少說一項、明說沒有、兩個陳述合併、無案件事實的對照組。"""
    by_stmt: dict[int, dict[str, Value]] = {}
    texts: dict[int, str] = {}
    for c in conds:
        by_stmt.setdefault(c.statement_id, {})[c.code] = satisfying(c)
        texts[c.statement_id] = c.text
    seen: set[frozenset[tuple[str, Value]]] = set()
    items: list[Item] = []

    def add(variant: str, gold: dict[str, Value], context: tuple[str, ...]) -> None:
        key = frozenset(gold.items())
        if gold and key not in seen:
            seen.add(key)
            items.append(Item(f"{variant}-{len(items):03d}", variant, gold, context))

    for sid, gold in by_stmt.items():
        ctx = (texts[sid],)
        add("full", dict(gold), ctx)
        codes = sorted(gold)
        if len(codes) >= 2:
            add("partial", {k: gold[k] for k in codes[:-1]}, ctx)
        flag = next((k for k in codes if vocab[k].kind == "flag"), None)
        if flag is not None:
            add("negated", {**{k: gold[k] for k in codes if k != flag}, flag: False}, ctx)
    rng = random.Random(seed)
    stmts = sorted(by_stmt)
    for _ in range(len(stmts)):
        a, b = rng.sample(stmts, 2)
        add("combined", {**by_stmt[a], **by_stmt[b]}, (texts[a], texts[b]))
    for i in range(controls):
        items.append(Item(f"none-{i:03d}", "none", {}))
    return items


def _describe(code: str, value: Value, vocab: dict[str, Concept]) -> str:
    c = vocab[code]
    if c.kind == "flag":
        return f"{c.name}：{'有發生' if value else '明說沒有發生'}"
    return f"{c.name}：{value:g}{c.unit or ''}"


def ask_prompt(item: Item, vocab: dict[str, Concept], index: int) -> str:
    """出題提示：標準答案改寫成口語；對照組只問一般性的法律知識。"""
    if not item.gold:
        topic = _CONTROL_TOPICS[index % len(_CONTROL_TOPICS)]
        return (
            f"你是環保法規的使用者。請寫一句向法規諮詢助理提出的問題，主題是「{topic}」。"
            "只問一般性的規定或知識，不得描述任何具體的事業、行為、數字或日期。只輸出問句。"
        )
    facts = "\n".join(f"- {_describe(k, v, vocab)}" for k, v in sorted(item.gold.items()))
    law = "\n".join(f"- {t[:200]}" for t in item.context)
    return (
        "你是一般民眾或事業負責人，遇到下列情況，要向法規諮詢助理提問。請寫一到兩句口語的問句，像真人會問的。\n"
        "規則：1. 每一項事實都要能從問句讀出；2. 用日常說法描述情境，不要照抄事實清單或條文的用語；"
        "3. 不得加入清單以外的任何事實、數字、日期或名稱；"
        "4. 標示「明說沒有發生」的要用否定的說法寫進問句；"
        "5. 不要出現概念代碼；6. 只輸出問句。\n"
        f"（以下條文只供你了解情境，問句不得引用條號或照抄條文）\n相關條文：\n{law}\n"
        f"事實：\n{facts}"
    )


def _candidate_lines(cands: dict[str, Concept]) -> str:
    lines = []
    for c in cands.values():
        shape = "旗標" if c.kind == "flag" else f"數值，單位 {c.unit or '無'}"
        lines.append(f"- {c.code}｜{c.name}｜{shape}")
    return "\n".join(lines)


_EXAMPLES = (
    "示範（概念與本題無關，只示範判斷方式）：\n"
    "示範候選：- night_work｜夜間施工｜旗標；- noise_over｜噪音超標｜旗標\n"
    "問句「我們工地昨晚有施工，噪音也量到超標，會怎樣？」→ night_work=true、noise_over=true\n"
    "問句「夜間施工是不是都要先申請？」→ 空（這是在問規定，沒有陳述事實）\n"
    "問句「我們沒有夜間施工，但鄰居說很吵，這樣違規嗎？」→ night_work=false；"
    "noise_over 沒提，不填\n"
)


def extract_prompt(question: str, cands: dict[str, Concept]) -> str:
    """逐概念判斷：對每個候選概念，問句是有、沒有、還是沒說。"""
    lines = _candidate_lines(cands)
    return (
        "你是法規諮詢的事實抽取器。逐一檢查下列每個候選概念，判斷問句對它的陳述屬於哪一種：\n"
        "- 有：問句明確說這件事已經發生或存在（數值概念要填問句裡的數字）\n"
        "- 沒有：問句明確說這件事沒有發生或不存在\n"
        "- 沒說：問句沒有明確說，包含只是詢問規定、要求、假設、可能性，或要靠推測才能得出\n"
        "規則：1. 預設是「沒說」，只有問句字面明確陳述才選有或沒有；"
        "2. 問句在問「是不是要…」「需不需要…」「能不能…」「如果…」時，被問的內容是「沒說」；"
        "3. 選有或沒有時附 quote：問句中的原文片段，必須逐字取自問句。\n"
        f"{_EXAMPLES}候選概念：\n{lines}\n問句：{question}\n"
        '只輸出 JSON，每個候選概念一項：{"concepts":[{"concept":"代碼","status":"有",'
        '"value":null,"quote":"原文片段"}]}（沒說的 quote 留空字串，value 只有數值概念要填）'
    )


class Fact(NamedTuple):
    concept: str
    value: Value
    quote: str


def _parse_exhaustive(rows: list[object]) -> list[Fact]:
    """v3 的逐概念判斷：「有」與「沒有」才成為事實，「沒說」略過。"""
    out: list[Fact] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code, status, quote, value = (
            row.get("concept"),
            row.get("status"),
            row.get("quote"),
            row.get("value"),
        )
        if not (isinstance(code, str) and isinstance(quote, str)):
            continue
        if status == "沒有":
            out.append(Fact(code, False, quote))
        elif status == "有":
            number = isinstance(value, int | float) and not isinstance(value, bool)
            out.append(Fact(code, float(value) if number else True, quote))  # type: ignore[arg-type]
    return out


def parse_facts(raw: str) -> list[Fact]:
    """取模型回覆裡第一個 JSON 物件的 facts；形狀不合的項目略過。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        doc = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if isinstance(doc, dict) and isinstance(doc.get("concepts"), list):
        return _parse_exhaustive(doc["concepts"])
    rows = doc.get("facts") if isinstance(doc, dict) else None
    out: list[Fact] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code, value, quote = row.get("concept"), row.get("value"), row.get("quote")
        if (
            isinstance(code, str)
            and isinstance(quote, str)
            and isinstance(value, bool | int | float)
        ):
            out.append(Fact(code, value if isinstance(value, bool) else float(value), quote))
    return out


_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000}
_NUMERAL = re.compile("[" + "".join(_DIGITS) + "".join(_UNITS) + "]+")


def _numeral_value(run: str) -> int:
    """一段中文數字的值：有位值字（十百千）按位值算，只有數字的逐位串接。"""
    if not any(ch in _UNITS for ch in run):
        return int("".join(str(_DIGITS[ch]) for ch in run))
    total = current = 0
    for ch in run:
        if ch in _DIGITS:
            current = _DIGITS[ch]
        else:
            total += (current or 1) * _UNITS[ch]
            current = 0
    return total + current


def arabic(text: str) -> str:
    """把問句裡的中文數字換成阿拉伯數字（「四個」「兩年」），讓數字檢查不因寫法而擋掉。"""
    return _NUMERAL.sub(lambda m: str(_numeral_value(m.group())), text)


def check(
    question: str, facts: list[Fact], vocab: dict[str, Concept]
) -> tuple[list[Fact], Counter[str]]:
    """程式檢查：概念在候選集、型別相符、原文片段在問句裡、數字在問句裡。回傳通過者與各項被擋的次數。"""
    flat = re.sub(r"\s+", "", question.replace(",", ""))
    digits = arabic(flat)
    kept: list[Fact] = []
    rejected: Counter[str] = Counter()
    for f in facts:
        concept = vocab.get(f.concept)
        if concept is None:
            rejected["概念不在候選集"] += 1
        elif (concept.kind == "flag") != isinstance(f.value, bool):
            rejected["值的型別不符"] += 1
        elif re.sub(r"\s+", "", f.quote) not in flat or not f.quote.strip():
            rejected["原文片段不在問句"] += 1
        elif not isinstance(f.value, bool) and f"{f.value:g}" not in digits:
            rejected["數字不在問句"] += 1
        else:
            kept.append(f)
    return kept, rejected


def audit_prompt(question: str, fact: Fact, vocab: dict[str, Concept]) -> str:
    """稽核提示：問句有沒有明確陳述這個事實（只看字面，不是「能不能推測」）。"""
    c = vocab[fact.concept]
    stated = (
        f"「{c.name}」{'成立或有發生' if fact.value else '明說沒有發生'}"
        if c.kind == "flag"
        else f"「{c.name}」為 {fact.value:g}{c.unit or ''}"
    )
    return (
        "請判斷下列問句有沒有明確陳述指定的事實。只看問句的字面意思；問句沒說、需要靠推測或法規知識才能得出的，都算「否」。\n"
        f"問句：{question}\n事實：{stated}\n只回答「是」或「否」。"
    )


class Cache:
    """模型回覆與嵌入向量的快取：同一提示或同一段文字不重複花費。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.vector_path = path.with_name(path.stem + ".vectors.json")
        self.data: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
        self.vectors: dict[str, list[float]] = (
            json.loads(self.vector_path.read_text()) if self.vector_path.exists() else {}
        )

    def get(self, model: ChatCompletionsModel, prompt: str) -> str:
        key = hashlib.sha256(f"{model.name}\n{prompt}".encode()).hexdigest()
        if key not in self.data:
            try:
                self.data[key] = model.complete(prompt)
            except ModelError as exc:
                self.data[key] = f"__error__ {exc}"
        return self.data[key]

    def embed(self, embedder: Embeds, texts: Sequence[str]) -> list[list[float]]:
        """每段文字的向量；沒算過的一次送出。"""
        keys = [hashlib.sha256(f"{embedder.model}\n{t}".encode()).hexdigest() for t in texts]
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in self.vectors}
        if missing:
            for key, vec in zip(missing, embedder.embed(list(missing.values())), strict=True):
                self.vectors[key] = vec
        return [self.vectors[k] for k in keys]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))
        self.vector_path.write_text(json.dumps(self.vectors))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """兩個向量的餘弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def nearest(
    vocab: dict[str, Concept], vectors: dict[str, list[float]], query: Sequence[float], k: int
) -> list[Concept]:
    """與查詢最相近的前 k 個概念（比的是概念名稱的向量）；k 為 0 是全部。"""
    ranked = sorted(vocab.values(), key=lambda c: -cosine(vectors[c.code], query))
    return ranked[:k] if k else ranked


def case_of(facts: dict[str, Value]) -> dict[str, float]:
    """交給推理機的案件事實：旗標為 1 或 0，數值照原值。"""
    return {k: (1.0 if v else 0.0) if isinstance(v, bool) else float(v) for k, v in facts.items()}


def judged(conn: Conn, as_of: date, facts: dict[str, Value]) -> tuple[dict[int, str], float]:
    """推理機的各陳述結論與耗時（秒）。"""
    t0 = time.perf_counter()
    result = evaluate(conn, as_of, case_of(facts))
    return dict(result.verdicts), time.perf_counter() - t0


def split_of(item: Item) -> str:
    """依標準答案雜湊切成開發集與測試集：調整只看開發集，最後才在測試集量一次。"""
    key = "|".join(sorted(item.gold)) or item.item_id
    return "dev" if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else "test"


def is_yes(reply: str) -> bool:
    """驗證者回覆是否為「是」。"""
    text = reply.strip()
    return text.startswith("是") or "：是" in reply[-6:] or text.endswith("是")


def main() -> None:
    """跑量測並寫出逐題結果與彙總。"""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, required=True, help="輸出目錄")
    ap.add_argument("--models", type=Path, default=Path("models.toml"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--seed", type=int, default=20261117)
    ap.add_argument("--controls", type=int, default=20, help="沒有案件事實的對照題數")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 題（試跑用）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument(
        "--candidates",
        type=int,
        default=12,
        help="每次給模型看的候選概念數，依問句由嵌入檢索；0 是全部（候選不依標準答案挑）",
    )
    ap.add_argument(
        "--extractor", default=EXTRACTOR_ID, help="抽取模型的識別；家族須不同於出題者與稽核者"
    )
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all")
    ap.add_argument(
        "--cache", type=Path, help="模型回覆快取檔，預設在輸出目錄；多次執行共用可省費用"
    )
    args = ap.parse_args()

    pick = {
        m.model_id: m
        for m in load_declared(args.models).values()
        if isinstance(m, ChatCompletionsModel)
    }
    extractor, asker = pick[args.extractor], pick[ASKER_ID]
    auditors = [pick[a] for a in AUDITOR_IDS]
    if extractor.family in {asker.family, *(a.family for a in auditors)}:
        raise SystemExit("抽取者必須與出題者、稽核者各屬不同家族")
    if len({a.family for a in auditors}) != len(auditors):
        raise SystemExit("兩位稽核者必須屬不同家族")
    embedder = load_embedder(args.models, timeout=60.0)
    if embedder is None:
        raise SystemExit("models.toml 沒有 [embedding]，無法檢索候選概念")
    args.out.mkdir(parents=True, exist_ok=True)
    cache = Cache(args.cache or args.out / "model-cache.json")

    with psycopg.connect(load(DatabaseSettings).dsn) as conn:
        conn.read_only = True
        conds = conditions(conn, args.as_of)
        vocab = vocabulary(conds)
        items = build_items(conds, vocab, args.seed, args.controls)
        if args.split != "all":
            items = [i for i in items if split_of(i) == args.split]
        if args.limit:
            items = items[: args.limit]
        asked_index = {
            i.item_id: n for n, i in enumerate(build_items(conds, vocab, args.seed, args.controls))
        }

        def questioned(item: Item) -> str:
            return cache.get(asker, ask_prompt(item, vocab, asked_index[item.item_id])).strip()

        concept_vectors = dict(
            zip(vocab, cache.embed(embedder, [c.name for c in vocab.values()]), strict=True)
        )

        def extract(question: str) -> tuple[list[Fact], dict[str, Concept]]:
            """抽出的事實（尚未檢查），與給模型看過的候選概念。"""
            query = cache.embed(embedder, [question])[0]
            shown = {c.code: c for c in nearest(vocab, concept_vectors, query, args.candidates)}
            return parse_facts(cache.get(extractor, extract_prompt(question, shown))), shown

        with ThreadPoolExecutor(args.workers) as pool:
            questions = list(pool.map(questioned, items))
            extracted = list(pool.map(extract, questions))
        parsed = [facts for facts, _ in extracted]
        cands = [shown for _, shown in extracted]
        checked = [check(q, f, c) for q, f, c in zip(questions, parsed, cands, strict=True)]

        def audited(pair: tuple[int, Fact]) -> list[bool]:
            prompt = audit_prompt(questions[pair[0]], pair[1], vocab)
            return [is_yes(cache.get(a, prompt)) for a in auditors]

        kept_by_item = [list(kept) for kept, _ in checked]
        pairs = [(i, f) for i, kept in enumerate(kept_by_item) for f in kept]
        with ThreadPoolExecutor(args.workers) as pool:
            votes = list(pool.map(audited, pairs))
        unsupported: dict[int, list[str]] = {}
        unsupported_by_all: dict[int, list[str]] = {}
        for (i, f), vote in zip(pairs, votes, strict=True):
            if not all(vote):
                unsupported.setdefault(i, []).append(f.concept)
            if not any(vote):
                unsupported_by_all.setdefault(i, []).append(f.concept)
        cache.save()

        reasoner = True
        try:
            judged(conn, args.as_of, {})
        except psycopg.errors.UndefinedFunction as exc:
            conn.rollback()
            reasoner = False
            print(f"推理機無法執行（資料庫綱要落後於程式），只量抽取：{exc}")

        rows = []
        for idx, (item, question, asked) in enumerate(zip(items, questions, parsed, strict=True)):
            kept, rejected = kept_by_item[idx], checked[idx][1]
            got = {f.concept: f.value for f in kept}
            gold_v, gold_t = judged(conn, args.as_of, item.gold) if reasoner else ({}, 0.0)
            got_v, got_t = judged(conn, args.as_of, got) if reasoner else ({}, 0.0)
            rows.append(
                {
                    "id": item.item_id,
                    "variant": item.variant,
                    "split": split_of(item),
                    "question": question,
                    "verbatim": sum(1 for k in item.gold if vocab[k].name in question),
                    "gold": item.gold,
                    "candidates": len(cands[idx]),
                    "gold_offered": sum(1 for g in item.gold if g in cands[idx]),
                    "raw_facts": [f._asdict() for f in asked],
                    "kept_facts": [f._asdict() for f in kept],
                    "rejected": dict(rejected),
                    "unsupported": unsupported.get(idx, []),
                    "unsupported_by_all": unsupported_by_all.get(idx, []),
                    "gold_verdicts": {str(k): v for k, v in gold_v.items()},
                    "got_verdicts": {str(k): v for k, v in got_v.items()},
                    "seconds": {"gold": round(gold_t, 4), "got": round(got_t, 4)},
                }
            )
    summary = summarize(rows, len(vocab), reasoner)
    summary["config"] = {
        "candidates": args.candidates,
        "split": args.split,
        "extractor": extractor.name,
        "auditors": [a.name for a in auditors],
    }
    (args.out / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def _decisive(verdicts: object) -> dict[str, str]:
    """明確的結論：排除「事實不明」與「不適用」。"""
    assert isinstance(verdicts, dict)
    return {k: v for k, v in verdicts.items() if v not in ("undetermined", "not_applicable")}


def summarize(rows: list[dict[str, object]], vocab_size: int, reasoner: bool) -> dict[str, object]:
    """逐題結果彙總：精確率、召回、憑空填值、推論結果一致率、對照組的行為。"""
    tp = fp = fn = ungrounded = raw_total = 0
    wrong_value = extra = unsupported_total = unsupported_all_total = 0
    by_variant: dict[str, Counter[str]] = {}
    same_verdicts = scored = 0
    recovered = contradicted = beyond_gold = gold_decisive = 0
    gold_total = offered_gold = 0
    for r in rows:
        gold = r["gold"]
        raw = r["raw_facts"]
        kept = r["kept_facts"]
        rejected = r["rejected"]
        assert isinstance(gold, dict) and isinstance(raw, list) and isinstance(kept, list)
        assert isinstance(rejected, dict)
        variant = str(r["variant"])
        c = by_variant.setdefault(variant, Counter())
        got = {f["concept"]: f["value"] for f in kept}
        raw_total += len(raw)
        unsup = r["unsupported"]
        unsup_all = r["unsupported_by_all"]
        assert isinstance(unsup, list) and isinstance(unsup_all, list)
        unsupported_total += len(unsup)
        unsupported_all_total += len(unsup_all)
        c["unsupported_facts"] += len(unsup)
        c["items_with_unsupported"] += int(bool(unsup))
        ungrounded += int(rejected.get("原文片段不在問句", 0))
        hit = sum(1 for k, v in got.items() if k in gold and gold[k] == v)
        tp += hit
        fp += len(got) - hit
        fn += len(gold) - hit
        wrong_value += sum(1 for k, v in got.items() if k in gold and gold[k] != v)
        extra += sum(1 for k in got if k not in gold)
        c["items"] += 1
        gold_offered = r["gold_offered"]
        assert isinstance(gold_offered, int)
        gold_total += len(gold)
        offered_gold += gold_offered
        verbatim = r["verbatim"]
        assert isinstance(verbatim, int)
        c["verbatim_facts"] += verbatim
        c["gold_facts"] += len(gold)
        c["with_any_fact"] += int(bool(got))
        c["exact"] += int(got == gold)
        if not reasoner:
            continue
        if gold:
            scored += 1
            same_verdicts += int(r["gold_verdicts"] == r["got_verdicts"])
            dg, dgot = _decisive(r["gold_verdicts"]), _decisive(r["got_verdicts"])
            gold_decisive += len(dg)
            recovered += sum(1 for k, v in dg.items() if dgot.get(k) == v)
            contradicted += sum(1 for k, v in dgot.items() if k in dg and dg[k] != v)
            beyond_gold += sum(1 for k in dgot if k not in dg)
        else:
            gv = r["got_verdicts"]
            assert isinstance(gv, dict)
            c["verdict_entries_if_run_anyway"] += len(gv)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "candidate_concepts": vocab_size,
        "items": len(rows),
        "gold_in_offered_candidates": {"offered": offered_gold, "gold": gold_total},
        "fact_level": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "wrong_value": wrong_value,
            "extra_concept_vs_gold": extra,
            "raw_facts": raw_total,
            "ungrounded_quote_rejected": ungrounded,
            "unsupported_by_any_audit": unsupported_total,
            "unsupported_by_all_audits": unsupported_all_total,
        },
        "downstream_same_verdicts": {
            "reasoner_ran": reasoner,
            "scored": scored,
            "same": same_verdicts,
            "gold_decisive_statements": gold_decisive,
            "recovered": recovered,
            "contradicted": contradicted,
            "beyond_gold": beyond_gold,
        },
        "by_variant": {k: dict(v) for k, v in sorted(by_variant.items())},
    }


if __name__ == "__main__":
    main()
