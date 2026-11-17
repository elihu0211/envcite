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
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple

import psycopg

from canon.db import Conn, fetch
from canon.reasoning.engine import evaluate
from canon.settings import DatabaseSettings, load
from canon.verification.config import load as load_models
from canon.verification.models import ChatCompletionsModel, ModelError

type Kind = Literal["flag", "number"]
type Value = bool | float

#: 抽取者與出題者的模型識別；家族必須不同（出題者不得與抽取者同家族）。
EXTRACTOR_ID = "mistral-small-4"
ASKER_ID = "muse-spark-open"
#: 逐一核對抽出的事實是否真被問句陳述的驗證者；家族不同於抽取者與出題者。
VERIFIER_ID = "nemotron-3.5-super-vl"

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
SELECT c.statement_id, k.code, k.name, c.comparator::text, c.threshold::float8, c.measure_unit,
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


def extract_prompt(question: str, vocab: dict[str, Concept]) -> str:
    """抽取提示：封閉詞彙、只填明說的、附原文片段。"""
    lines = []
    for c in vocab.values():
        shape = "旗標" if c.kind == "flag" else f"數值，單位 {c.unit or '無'}"
        lines.append(f"- {c.code}｜{c.name}｜{shape}")
    return (
        "你是法規諮詢的事實抽取器。從使用者的問句中找出使用者明確陳述的案件事實，對應到下列候選概念。\n"
        "規則：\n"
        "1. 只填問句明確說出的事實；問句沒說的、你推測的、由法規知識推得的，一律不填。\n"
        "2. concept 只能用候選清單的代碼，不得自創；對不到就不填。\n"
        "3. 旗標概念的 value 是 true（問句說有發生或成立）或 false（問句明說沒有發生）；"
        "數值概念的 value 是問句裡的數字。\n"
        "4. 每個事實附 quote：問句中的原文片段，必須逐字出現在問句裡。\n"
        "5. 問句沒有任何案件事實（例如只問規定、定義、主管機關）時，facts 回傳空陣列。\n"
        "候選概念：\n" + "\n".join(lines) + f"\n問句：{question}\n"
        '只輸出 JSON：{"facts":[{"concept":"代碼","value":true,"quote":"原文片段"}]}'
    )


class Fact(NamedTuple):
    concept: str
    value: Value
    quote: str


def parse_facts(raw: str) -> list[Fact]:
    """取模型回覆裡第一個 JSON 物件的 facts；形狀不合的項目略過。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        doc = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
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


def check(
    question: str, facts: list[Fact], vocab: dict[str, Concept]
) -> tuple[list[Fact], Counter[str]]:
    """程式檢查：概念在候選集、型別相符、原文片段在問句裡、數字在問句裡。回傳通過者與各項被擋的次數。"""
    flat = re.sub(r"\s+", "", question.replace(",", ""))
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
        elif not isinstance(f.value, bool) and f"{f.value:g}" not in flat:
            rejected["數字不在問句"] += 1
        else:
            kept.append(f)
    return kept, rejected


def verify_prompt(question: str, fact: Fact, vocab: dict[str, Concept]) -> str:
    """驗證提示：問句有沒有明確陳述這個事實（不是「能不能推測」）。"""
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
    """模型回覆快取：同一提示不重複花費。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, model: ChatCompletionsModel, prompt: str) -> str:
        key = hashlib.sha256(f"{model.name}\n{prompt}".encode()).hexdigest()
        if key not in self.data:
            try:
                self.data[key] = model.complete(prompt)
            except ModelError as exc:
                self.data[key] = f"__error__ {exc}"
        return self.data[key]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))


def case_of(facts: dict[str, Value]) -> dict[str, float]:
    """交給推理機的案件事實：旗標為 1 或 0，數值照原值。"""
    return {k: (1.0 if v else 0.0) if isinstance(v, bool) else float(v) for k, v in facts.items()}


def judged(conn: Conn, as_of: date, facts: dict[str, Value]) -> tuple[dict[int, str], float]:
    """推理機的各陳述結論與耗時（秒）。"""
    t0 = time.perf_counter()
    result = evaluate(conn, as_of, case_of(facts))
    return dict(result.verdicts), time.perf_counter() - t0


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
    args = ap.parse_args()

    models = load_models(args.models).everyone
    pick = {m.model_id: m for m in models if isinstance(m, ChatCompletionsModel)}
    extractor, asker, verifier = pick[EXTRACTOR_ID], pick[ASKER_ID], pick[VERIFIER_ID]
    if len({extractor.family, asker.family, verifier.family}) != 3:
        raise SystemExit("抽取者、出題者與驗證者必須各屬不同家族")
    args.out.mkdir(parents=True, exist_ok=True)
    cache = Cache(args.out / "model-cache.json")

    with psycopg.connect(load(DatabaseSettings).dsn) as conn:
        conn.read_only = True
        conds = conditions(conn, args.as_of)
        vocab = vocabulary(conds)
        items = build_items(conds, vocab, args.seed, args.controls)
        if args.limit:
            items = items[: args.limit]

        def questioned(pair: tuple[int, Item]) -> str:
            return cache.get(asker, ask_prompt(pair[1], vocab, pair[0])).strip()

        with ThreadPoolExecutor(args.workers) as pool:
            questions = list(pool.map(questioned, enumerate(items)))
            raws = list(
                pool.map(lambda q: cache.get(extractor, extract_prompt(q, vocab)), questions)
            )
        checked = [
            check(q, parse_facts(raw), vocab)[0] for q, raw in zip(questions, raws, strict=True)
        ]
        pairs = [(i, f) for i, kept in enumerate(checked) for f in kept]

        def supported(pair: tuple[int, Fact]) -> bool:
            reply = cache.get(verifier, verify_prompt(questions[pair[0]], pair[1], vocab))
            return (
                reply.strip().startswith("是")
                or "：是" in reply[-6:]
                or reply.strip().endswith("是")
            )

        with ThreadPoolExecutor(args.workers) as pool:
            verdicts = list(pool.map(supported, pairs))
        unsupported: dict[int, list[str]] = {}
        for (i, f), ok in zip(pairs, verdicts, strict=True):
            if not ok:
                unsupported.setdefault(i, []).append(f.concept)
        cache.save()

        reasoner = True
        try:
            judged(conn, args.as_of, {})
        except psycopg.errors.UndefinedFunction as exc:
            conn.rollback()
            reasoner = False
            print(f"推理機無法執行（資料庫綱要落後於程式），只量抽取：{exc}")

        rows = []
        for idx, (item, question, raw) in enumerate(zip(items, questions, raws, strict=True)):
            asked = parse_facts(raw)
            kept, rejected = check(question, asked, vocab)
            got = {f.concept: f.value for f in kept}
            gold_v, gold_t = judged(conn, args.as_of, item.gold) if reasoner else ({}, 0.0)
            got_v, got_t = judged(conn, args.as_of, got) if reasoner else ({}, 0.0)
            rows.append(
                {
                    "id": item.item_id,
                    "variant": item.variant,
                    "question": question,
                    "verbatim": sum(1 for k in item.gold if vocab[k].name in question),
                    "gold": item.gold,
                    "raw_facts": [f._asdict() for f in asked],
                    "kept_facts": [f._asdict() for f in kept],
                    "rejected": dict(rejected),
                    "unsupported": unsupported.get(idx, []),
                    "gold_verdicts": {str(k): v for k, v in gold_v.items()},
                    "got_verdicts": {str(k): v for k, v in got_v.items()},
                    "seconds": {"gold": round(gold_t, 4), "got": round(got_t, 4)},
                }
            )
    summary = summarize(rows, len(vocab), reasoner)
    (args.out / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def summarize(rows: list[dict[str, object]], vocab_size: int, reasoner: bool) -> dict[str, object]:
    """逐題結果彙總：精確率、召回、憑空填值、推論結果一致率、對照組的行為。"""
    tp = fp = fn = ungrounded = raw_total = 0
    wrong_value = extra = unsupported_total = 0
    by_variant: dict[str, Counter[str]] = {}
    same_verdicts = scored = 0
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
        assert isinstance(unsup, list)
        unsupported_total += len(unsup)
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
        else:
            gv = r["got_verdicts"]
            assert isinstance(gv, dict)
            c["verdict_entries_if_run_anyway"] += len(gv)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "candidate_concepts": vocab_size,
        "items": len(rows),
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
            "unsupported_by_verifier": unsupported_total,
        },
        "downstream_same_verdicts": {
            "reasoner_ran": reasoner,
            "scored": scored,
            "same": same_verdicts,
        },
        "by_variant": {k: dict(v) for k, v in sorted(by_variant.items())},
    }


if __name__ == "__main__":
    main()
