"""Graded relevance judgements by two models for the golden set (UMBRELA, adapted), no person.

    uv run python tools/relevance_judge.py calibrate <golden.yaml> --qrels eval/golden-qrels.json \\
        --split eval/golden-split.json --cache <outside the repo>/judge-cache.jsonl \\
        --out eval/golden-calibration-<date>.json
    uv run python tools/relevance_judge.py pool <golden.yaml> --qrels eval/golden-qrels.json \\
        --split eval/golden-split.json --subset dev --run <a>.run.json [--run …] \\
        --calibration eval/golden-calibration-<date>.json --cache … \\
        --out eval/golden-judgements-dev-<date>.json

The prompt is UMBRELA's zero-shot Bing prompt (Upadhyay et al. 2024, the descriptive–narrative–
aspect structure of Thomas et al. 2023) in Traditional Chinese for statutes: the question, the law's
name and article number, the version in force on `--as-of` (or, when none has a confirmed
commencement, the latest promulgated one, said so) and the article's full text, scored 0–3
with the intent, M (match), T (trust) and O (final) steps. It asks for one JSON object and a short
reason; the endpoint is asked for that JSON schema at temperature 0. The template, its sha256, the
schema, the judges' names and families and the temperature go into every file written here, and the
template's sha256 is part of what a judgement file's own sha256 covers.

The judges come from `[relevance]` in the model configuration (`config.load_judges`): at least two,
of different families, none of a family on the measured path (retrieval and answering). Before
judging, every judge and every role model of a system family is asked the same open questions;
identical answers stop the run (`SameModel`), which catches a hosted alias of a system model.
A reply naming another model than the one asked for (a gateway's fallback) is a failed call, not a
judgement. Nothing judged here reranks a retrieval.

Each article is judged once by each judge. The final grade is the lower of the two (conservative);
an article that either judge could not grade stays a hole. Agreement is reported as Cohen's κ on
relevance ≥ `relevant_from` and quadratic-weighted κ on the grades, with the share of disagreements.

`calibrate` judges, on dev only, the golden set's expected articles (positives) and one random
article of the same law per positive that the golden set does not list for that question
(negatives, seeded), and reports each judge's and the conservative grade's recall on the positives
and false-positive rate on the negatives. The prompt is tuned only on this; once it is fixed, a
`pool` run must name a calibration file made with the same template, so test is judged only by a
prompt frozen on dev.

`pool` judges the holes of the given runs (unjudged articles in a question's top 10) and the
subset's expected articles that no model has graded yet, and writes a sealed judgement file;
`golden_set.py judge` merges it into qrels. Question texts are not written (the golden set is
external); each row keeps the sha256 of the article text it was judged on. Calls are cached in a
JSONL file outside the repository so a run that stops can resume; the cache holds question text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_set as gs

from envcite_ontology.settings import DatabaseSettings, ModelsSettings, load
from envcite_ontology.verification.config import load as load_models
from envcite_ontology.verification.config import load_judges
from envcite_ontology.verification.distinct import assert_distinct
from envcite_ontology.verification.models import ChatCompletionsModel, Model, ModelError

PROMPT = """\
給定一個關於臺灣法規的問題與一條法規條文，你必須以 0 到 3 的整數評分，意義如下：
0 = 條文與問題無關；
1 = 條文看似與問題相關，但沒有回答問題；
2 = 條文含有問題的部分答案，但答案可能不夠明確，或夾雜在其他規定之中；
3 = 條文專門處理這個問題，且含有確切的答案。

重要說明：條文只與主題部分相關、但不完全時給 1；\
條文對整個問題提出很重要的規定、但也含其他內容時給 2；\
條文完全且只針對這個問題時給 3。以上皆不符合時給 0。
法規問題的答案常由數條條文共同構成（例如義務條與罰則條、用語定義與要件）：條文本身就提供回答所需的\
其中一部分規定時，算「含有部分答案」。問題問數條條文或數部法規之間的關聯時，問題點名的條文本身，\
以及引用它或被它引用的條文，都是回答所需的一部分。只依下面這條條文的文字判斷，不依你記得的其他條文。

問題：{question}
條文：{title}第{number}條（{version}）
{text}

把這個問題拆成步驟：
考慮提問的根本意圖（intent）。
衡量條文內容與問題可能意圖的吻合程度（M）。
衡量條文的可信度（T）。
綜合以上各項及其相對重要性，決定最終分數（O）；最終分數必須是整數。
不要輸出程式碼。只輸出一個 JSON 物件，格式為：\
{{"intent": "一句話", "M": 整數, "T": 整數, "O": 整數, "reason": "四十字以內，說明 O"}}
"""

PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "M": {"type": "integer", "enum": list(gs.SCALE)},
        "T": {"type": "integer", "enum": list(gs.SCALE)},
        "O": {"type": "integer", "enum": list(gs.SCALE)},
        "reason": {"type": "string"},
    },
    "required": ["intent", "M", "T", "O", "reason"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "relevance", "strict": True, "schema": SCHEMA},
}
TEMPERATURE = 0
#: 回覆不是合格 JSON 時再問的次數（溫度 0 也可能因端點的批次而不同）
PARSE_RETRIES = 2
#: 一位判定者連續失敗這麼多條就停止問它（端點故障）；沒判的條不寫快取，下次續跑
BREAKER = 20

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class Verdict:
    intent: str
    M: int
    T: int
    O: int  # noqa: E741 — UMBRELA 的欄名
    reason: str


def parse_verdict(text: str) -> Verdict:
    """模型回覆 → `Verdict`；不是一個含 0–3 整數 M、T、O 的 JSON 物件就 ValueError。

    容許外圍的 Markdown 程式碼圍欄與物件前後的文字（推理模型常在前面說話），取第一個 `{` 到最後
    一個 `}`。
    """
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("回覆裡沒有 JSON 物件")
    try:
        obj = json.loads(body[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"回覆不是合格的 JSON：{e}") from None
    if not isinstance(obj, dict):
        raise ValueError("回覆不是 JSON 物件")
    scores: dict[str, int] = {}
    for key in ("M", "T", "O"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, bool) or not isinstance(v, int) or v not in gs.SCALE:
            raise ValueError(f"{key} 不是 0–3 的整數：{v!r}")
        scores[key] = v
    return Verdict(
        intent=str(obj.get("intent") or "").strip(),
        M=scores["M"],
        T=scores["T"],
        O=scores["O"],
        reason=str(obj.get("reason") or "").strip(),
    )


def version_note(as_of: str, version_date: str, in_force: bool) -> str:
    """提示裡的版本說明；該日沒有確認施行的版本時，明說給的是最新公布的版本。"""
    if in_force:
        return f"{as_of} 現行的版本，公布或修正日 {version_date}"
    return f"{as_of} 尚無確認施行的版本；以下是 {version_date} 公布的最新版本"


def render(question: str, title: str, number: str, version: str, text: str) -> str:
    return PROMPT.format(
        question=question.strip(), title=title, number=number, version=version, text=text.strip()
    )


def conservative(grades: Iterable[int | None]) -> int | None:
    """各判定者分數的最小值；任一判定者沒有分數時為 None（仍是洞）。"""
    xs = list(grades)
    if not xs or any(g is None for g in xs):
        return None
    return min(g for g in xs if g is not None)


def cohen_kappa(a: Sequence[int], b: Sequence[int]) -> float | None:
    """兩位判定者在同一批項目上的 Cohen's κ（名目類別）；沒有項目或機率一致度為 1 時為 None。"""
    if len(a) != len(b):
        raise ValueError("兩組判定的長度不同")
    n = len(a)
    if not n:
        return None
    cats = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe)


def weighted_kappa(
    a: Sequence[int], b: Sequence[int], categories: Sequence[int] = gs.SCALE
) -> float | None:
    """二次加權 κ：不一致的代價是 (i − j)² ／ (K − 1)²；期望值由兩位判定者各自的邊際分布得出。"""
    if len(a) != len(b):
        raise ValueError("兩組判定的長度不同")
    n = len(a)
    if not n:
        return None
    k = len(categories)
    index = {c: i for i, c in enumerate(categories)}

    def w(i: int, j: int) -> float:
        return (i - j) ** 2 / (k - 1) ** 2

    observed = sum(w(index[x], index[y]) for x, y in zip(a, b, strict=True))
    pa = [sum(index[x] == i for x in a) / n for i in range(k)]
    pb = [sum(index[y] == j for y in b) / n for j in range(k)]
    expected = n * sum(w(i, j) * pa[i] * pb[j] for i in range(k) for j in range(k))
    return None if expected == 0 else 1 - observed / expected


def agreement(
    grades: Mapping[str, Sequence[int | None]], relevant_from: int = gs.RELEVANT_FROM
) -> dict[str, Any]:
    """兩位判定者的一致性：只算兩者都有分數的項目。"""
    names = list(grades)
    if len(names) != 2:
        raise ValueError("一致性只對兩位判定者計算")
    pairs = [
        (x, y)
        for x, y in zip(grades[names[0]], grades[names[1]], strict=True)
        if x is not None and y is not None
    ]
    a = [x for x, _ in pairs]
    b = [y for _, y in pairs]
    ba = [int(x >= relevant_from) for x in a]
    bb = [int(y >= relevant_from) for y in b]
    n = len(pairs)

    def r(x: float | None) -> float | None:
        return None if x is None else round(x, 4)

    return {
        "judges": names,
        "n": n,
        "relevant_from": relevant_from,
        "cohen_kappa_binary": r(cohen_kappa(ba, bb)),
        "weighted_kappa_quadratic": r(weighted_kappa(a, b)),
        "disagree_binary": r(sum(x != y for x, y in zip(ba, bb, strict=True)) / n if n else None),
        "disagree_graded": r(sum(x != y for x, y in pairs) / n if n else None),
        "confusion": {
            f"{x}-{y}": sum(p == (x, y) for p in pairs) for x in gs.SCALE for y in gs.SCALE
        },
    }


class Judge(ChatCompletionsModel):
    """判定用的轉接器：回覆的模型名與所問的不同（閘道的備援）視為失敗；呼叫數與詞元另記，執行緒安全。"""

    def __init__(self, base: ChatCompletionsModel) -> None:
        super().__init__(
            base.model_id,
            max_tokens=base.max_tokens,
            timeout=base.timeout,
            family=base.family,
            base_url=base._base_url,
            api_key=base._api_key,
            max_retries=base.max_retries,
        )
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "failures": 0}

    def _post(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = super()._post(body)
        served = payload.get("model")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        with self._lock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        if served is not None and served != self.model_id:
            raise ModelError(f"{self.name}: the endpoint answered as {served!r}")
        return payload

    def failed(self) -> None:
        with self._lock:
            self.usage["failures"] += 1


@dataclass(frozen=True)
class Item:
    qid: str
    doc: str
    question: str
    title: str
    version_date: str
    text: str
    in_force: bool = True

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def cache_key(judge: str, item: Item) -> str:
    raw = "\n".join(
        (PROMPT_SHA256, judge, item.qid, item.doc, item.text_sha256, gs_sha(item.question))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def gs_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Cache:
    """JSONL：鍵 → 判定（或失敗原因）。只附加；同鍵以最後一筆為準。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.rows: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.rows[row["key"]] = row
        path.parent.mkdir(parents=True, exist_ok=True)

    def put(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.rows[row["key"]] = row
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def ask(judge: Judge, item: Item, as_of: str) -> dict[str, Any]:
    """一位判定者對一條的判定；呼叫或解析失敗回傳 `error`，不拋出。"""
    _, number = gs.parse_doc(item.doc)
    version = version_note(as_of, item.version_date, item.in_force)
    prompt = render(item.question, item.title, number, version, item.text)
    error = ""
    for _ in range(PARSE_RETRIES + 1):
        try:
            reply = judge.complete(prompt, response_format=RESPONSE_FORMAT)
        except ModelError as e:
            error = str(e)[:300]
            continue
        try:
            return {"verdict": asdict(parse_verdict(reply))}
        except ValueError as e:
            error = f"{e}；回覆前 200 字：{reply[:200]!r}"
    judge.failed()
    return {"error": error}


def judge_all(
    items: Sequence[Item],
    judges: Sequence[Judge],
    cache: Cache,
    *,
    as_of: str,
    workers: int,
    retry_failed: bool = True,
    log: Callable[[str], None] = print,
) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
    """每條由每位判定者各判一次（快取命中的不再問）：（題號, 條）→ 判定者 → 判定或錯誤。

    `workers` 是每位判定者的並行數。
    """
    todo = []
    for item in items:
        for j in judges:
            hit = cache.rows.get(cache_key(j.name, item))
            if hit is None or (retry_failed and "error" in hit):
                todo.append((item, j))
    log(
        f"{len(items)} 條 × {len(judges)} 位判定者；快取已有 {len(items) * len(judges) - len(todo)}"
    )
    done = 0
    lock = threading.Lock()
    streak = dict.fromkeys((j.name for j in judges), 0)
    tripped: set[str] = set()

    def work(pair: tuple[Item, Judge]) -> None:
        nonlocal done
        item, j = pair
        if j.name in tripped:
            return
        got = ask(j, item, as_of)
        with lock:
            streak[j.name] = streak[j.name] + 1 if "error" in got else 0
            if streak[j.name] >= BREAKER and j.name not in tripped:
                tripped.add(j.name)
                log(f"  {j.name} 連續 {BREAKER} 次失敗，本次不再問它：{got['error'][:120]}")
        cache.put(
            {
                "key": cache_key(j.name, item),
                "judge": j.name,
                "qid": item.qid,
                "doc": item.doc,
                **got,
            }
        )
        with lock:
            done += 1
            if done % 200 == 0 or done == len(todo):
                log(f"  {done}/{len(todo)}")

    # 每位判定者各自一個執行緒池：慢或故障的判定者不占另一位的名額
    pools = [ThreadPoolExecutor(max_workers=workers) for _ in judges]
    try:
        futures = [pools[judges.index(j)].submit(work, (item, j)) for item, j in todo]
        for f in futures:
            f.result()
    finally:
        for pool in pools:
            pool.shutdown()
    out: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for item in items:
        out[(item.qid, item.doc)] = {
            j.name: {
                k: v
                for k, v in cache.rows.get(
                    cache_key(j.name, item), {"error": "未判（斷路）"}
                ).items()
                if k in ("verdict", "error")
            }
            for j in judges
        }
    return out


def grade_of(result: Mapping[str, Any]) -> int | None:
    v = result.get("verdict")
    return None if v is None else int(v["O"])


def rows_of(
    items: Sequence[Item],
    results: Mapping[tuple[str, str], Mapping[str, Mapping[str, Any]]],
    judges: Sequence[Judge],
    upstream: Mapping[str, set[str]],
    labels: Mapping[tuple[str, str], str] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        res = results[(item.qid, item.doc)]
        grades = {j.name: grade_of(res[j.name]) for j in judges}
        row: dict[str, Any] = {
            "qid": item.qid,
            "doc": item.doc,
            "upstream": item.doc in upstream.get(item.qid, set()),
            "text_sha256": item.text_sha256,
            "in_force": item.in_force,
            "grades": grades,
            "grade": conservative(grades.values()),
            "verdicts": {j.name: res[j.name].get("verdict") for j in judges},
            "errors": {j.name: res[j.name]["error"] for j in judges if "error" in res[j.name]},
        }
        if labels is not None:
            row["label"] = labels[(item.qid, item.doc)]
        rows.append(row)
    return rows


def rates(rows: Sequence[Mapping[str, Any]], judges: Sequence[str], floor: int) -> dict[str, Any]:
    """校準：各判定者與保守分數對正例的召回、對負例的誤判（≥ floor 為相關；沒有分數的不計）。"""
    out: dict[str, Any] = {}
    for who in (*judges, "conservative"):

        def grade(r: Mapping[str, Any]) -> int | None:
            return r["grade"] if who == "conservative" else r["grades"][who]  # noqa: B023

        pos = [g for r in rows if r["label"] == "positive" and (g := grade(r)) is not None]
        neg = [g for r in rows if r["label"] == "negative" and (g := grade(r)) is not None]
        out[who] = {
            "positives": len(pos),
            "recall": round(sum(g >= floor for g in pos) / len(pos), 4) if pos else None,
            "negatives": len(neg),
            "false_positive_rate": round(sum(g >= floor for g in neg) / len(neg), 4)
            if neg
            else None,
            "positive_grades": {str(g): pos.count(g) for g in gs.SCALE},
            "negative_grades": {str(g): neg.count(g) for g in gs.SCALE},
        }
    return out


def texts_at(
    conn: psycopg.Connection[Any], docs: Iterable[str], on: str
) -> dict[str, tuple[str, str, str, bool]]:
    """條文代號 → （法規名稱, 版本日, 條文全文, 該日是否確認施行）。

    該日有確認施行的版本就用它；沒有（例如修正條文的施行日未確認）時用該日以前公布的最新版本，
    並記為未確認施行，提示會照實說明。
    """
    wanted = set(docs)
    out = {d: (*t, True) for d, t in gs.article_texts(conn, wanted, on).items() if t[2].strip()}
    rest = wanted - set(out)
    if not rest:
        return out
    for pcode, title, version, number, text in conn.execute(
        "SELECT DISTINCT ON (r.local_id, p.path) r.local_id, e.title, e.version_date,"
        "       corpus.article_number(p.path), corpus.article_text(v.id)"
        "  FROM corpus.provision AS p"
        "  JOIN corpus.legal_resource AS r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL"
        "  JOIN corpus.legal_expression AS e ON e.id = v.expression_id"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(%(pcodes)s)"
        "   AND p.provision_type = 'article' AND e.language = 'zho'"
        "   AND e.version_date <= %(on)s::date"
        " ORDER BY r.local_id, p.path, e.version_date DESC",
        {"on": on, "pcodes": sorted({gs.parse_doc(d)[0] for d in rest})},
    ):
        d = gs.doc_id(str(pcode), str(number))
        if d in rest and (text or "").strip():
            out[d] = (str(title), str(version), str(text), False)
    return out


def law_articles(
    conn: psycopg.Connection[Any], pcodes: Iterable[str], on: str
) -> dict[str, list[str]]:
    """法規 → 該日現行的條文代號（依代號排序）。"""
    out: dict[str, list[str]] = defaultdict(list)
    for pcode, number in conn.execute(
        "SELECT r.local_id, corpus.article_number(s.path)"
        "  FROM corpus.legal_resource AS r"
        " CROSS JOIN LATERAL corpus.provisions_at(r.id, 'zho', %(on)s::date) AS s"
        " WHERE r.id_scheme = 'moj_pcode' AND r.local_id = ANY(%(pcodes)s)"
        "   AND s.provision_type = 'article'",
        {"on": on, "pcodes": sorted(set(pcodes))},
    ):
        out[str(pcode)].append(gs.doc_id(str(pcode), str(number)))
    return {p: sorted(ds) for p, ds in out.items()}


def negatives_for(
    upstream: Mapping[str, Sequence[str]],
    articles: Mapping[str, Sequence[str]],
    *,
    seed: str,
) -> list[tuple[str, str]]:
    """每個正例配一條同法規、題庫沒有列給這題的條；以（種子, 題號, 正例）決定，不放回。"""
    out = []
    for q in sorted(upstream):
        listed = set(upstream[q])
        taken: set[str] = set()
        for d in sorted(listed):
            pcode, _ = gs.parse_doc(d)
            pool = [a for a in articles.get(pcode, []) if a not in listed and a not in taken]
            if not pool:
                continue
            pick = random.Random(f"{seed}/{q}/{d}").choice(pool)
            taken.add(pick)
            out.append((q, pick))
    return out


def build_items(
    pairs: Iterable[tuple[str, str]],
    questions: Mapping[str, str],
    texts: Mapping[str, tuple[str, str, str, bool]],
) -> tuple[list[Item], list[tuple[str, str]]]:
    """（可判的條, 該日沒有現行條文而不判的（題號, 條））。"""
    items, missing = [], []
    for q, d in pairs:
        got = texts.get(d)
        if got is None:
            missing.append((q, d))
            continue
        title, version, text, in_force = got
        items.append(Item(q, d, questions[q], title, version, text, in_force))
    return items, missing


def setup(args: argparse.Namespace) -> tuple[list[Judge], dict[str, Any]]:
    """判定者（已過獨立性檢查）與寫進檔案的判定者描述。"""
    path = Path(load(ModelsSettings).models or "models.toml")
    configured = load_judges(path)
    judges = []
    for m in configured.models:
        if not isinstance(m, ChatCompletionsModel):
            raise SystemExit(
                f"{m.name}: 判定者只支援 OpenAI 相容端點（要能指定溫度與 JSON schema）"
            )
        judges.append(Judge(m))
    if not args.no_distinct_check:
        names = {j.name for j in judges}
        system: list[Model] = [
            m
            for m in load_models(path).everyone
            if m.family in configured.system_families and m.name not in names
        ]
        assert_distinct([*judges, *system])
    described = {
        "judges": [
            {
                "name": j.name,
                "model_id": j.model_id,
                "family": j.family,
                "temperature": TEMPERATURE,
                "max_tokens": j.max_tokens,
            }
            for j in judges
        ],
        "system_families": sorted(configured.system_families),
        "distinct_check": not args.no_distinct_check,
    }
    return judges, described


def prompt_record() -> dict[str, Any]:
    return {
        "method": "UMBRELA qrel_zeroshot_bing（castorini/umbrela），繁體中文法規語境改寫；"
        "輸出改為含理由的 JSON",
        "template": PROMPT,
        "sha256": PROMPT_SHA256,
        "response_format": RESPONSE_FORMAT,
        "parse_retries": PARSE_RETRIES,
    }


def _golden(path: Path) -> tuple[dict[str, str], str]:
    raw = path.read_bytes()
    items = yaml.safe_load(raw)["items"]
    return {str(i["id"]): str(i["question"]) for i in items}, hashlib.sha256(raw).hexdigest()


def _usage(judges: Sequence[Judge]) -> dict[str, Any]:
    return {j.name: dict(j.usage) for j in judges}


def cmd_calibrate(args: argparse.Namespace) -> None:
    questions, golden_sha = _golden(args.golden)
    qrels_doc, split_doc = gs.read(args.qrels), gs.read(args.split)
    gs.require_golden(qrels_doc, golden_sha, str(args.qrels))
    gs.require_golden(split_doc, golden_sha, str(args.split))
    dev = gs.subset_ids(split_doc, "dev")
    upstream = {q: ds for q, ds in qrels_doc["upstream"].items() if q in dev}
    judges, described = setup(args)
    on = args.as_of
    db = load(DatabaseSettings)
    with psycopg.connect(db.dsn) as conn:
        conn.read_only = True
        articles = law_articles(
            conn, {gs.parse_doc(d)[0] for ds in upstream.values() for d in ds}, on
        )
        negatives = negatives_for(upstream, articles, seed=args.seed)
        positives = [(q, d) for q in sorted(upstream) for d in upstream[q]]
        texts = texts_at(conn, {d for _, d in positives + negatives}, on)
    labels = {**{p: "positive" for p in positives}, **{n: "negative" for n in negatives}}
    items, missing = build_items([*positives, *negatives], questions, texts)
    results = judge_all(items, judges, Cache(args.cache), as_of=on, workers=args.workers)
    up = {q: set(ds) for q, ds in upstream.items()}
    rows = rows_of(items, results, judges, up, labels)
    names = [j.name for j in judges]
    floor = int(qrels_doc["relevant_from"])
    doc = gs.write(
        args.out,
        {
            "kind": "calibration",
            "subset": "dev",
            "golden_sha256": golden_sha,
            "qrels_sha256": qrels_doc["sha256"],
            "split_sha256": split_doc["sha256"],
            "date": date.today().isoformat(),
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "as_of": on,
            "prompt": prompt_record(),
            **described,
            "relevant_from": floor,
            "negatives": {
                "rule": "每個正例配一條同法規、題庫沒有列給這題的現行條；"
                "random.Random('<seed>/<題號>/<正例>') 不放回；負例只是假定不相關",
                "seed": args.seed,
            },
            "counts": {
                "positives": len(positives),
                "negatives": len(negatives),
                "no_text": len(missing),
            },
            "rates": rates(rows, names, floor),
            "agreement": agreement({n: [r["grades"][n] for r in rows] for n in names}, floor),
            "usage": _usage(judges),
            "rows": rows,
        },
    )
    print(
        json.dumps(
            {k: doc[k] for k in ("counts", "rates", "agreement", "usage")},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(f"{args.out}：sha256 {doc['sha256']}")


def cmd_pool(args: argparse.Namespace) -> None:
    questions, golden_sha = _golden(args.golden)
    qrels_doc, split_doc = gs.read(args.qrels), gs.read(args.split)
    gs.require_golden(qrels_doc, golden_sha, str(args.qrels))
    gs.require_golden(split_doc, golden_sha, str(args.split))
    calibration = gs.read(args.calibration)
    if calibration.get("kind") != "calibration" or calibration["subset"] != "dev":
        raise SystemExit(f"{args.calibration}: 不是開發集的校準檔")
    if calibration["prompt"]["sha256"] != PROMPT_SHA256:
        raise SystemExit("提示與校準時的不同：先在開發集重新校準，再判定（提示只在開發集上調）")
    runs = [gs.read(p) for p in args.run]
    for p, r in zip(args.run, runs, strict=True):
        gs.require_golden(r, golden_sha, str(p))
        if r["split_sha256"] != split_doc["sha256"] or r["subset"] != args.subset:
            raise SystemExit(f"{p}: 不是 {args.subset} 子集的 run")
    subset = gs.subset_ids(split_doc, args.subset)
    qrels = {q: d for q, d in qrels_doc["qrels"].items() if q in subset}
    graded_by = qrels_doc["graded_by"]
    holes = gs.pool_rows(qrels, [r["ranking"] for r in runs], args.depth)
    ungraded = [
        (q, d)
        for q in sorted(qrels)
        for d in qrels_doc["upstream"][q]
        if d not in graded_by.get(q, {})
    ]
    pairs = sorted(set(holes) | set(ungraded))
    judges, described = setup(args)
    if [j["name"] for j in described["judges"]] != [j["name"] for j in calibration["judges"]]:
        raise SystemExit("判定者與校準時的不同")
    on = args.as_of
    db = load(DatabaseSettings)
    with psycopg.connect(db.dsn) as conn:
        conn.read_only = True
        texts = texts_at(conn, {d for _, d in pairs}, on)
    items, missing = build_items(pairs, questions, texts)
    results = judge_all(items, judges, Cache(args.cache), as_of=on, workers=args.workers)
    up = {q: set(ds) for q, ds in qrels_doc["upstream"].items()}
    rows = rows_of(items, results, judges, up)
    rows += [
        {
            "qid": q,
            "doc": d,
            "upstream": d in up.get(q, set()),
            "grade": None,
            "errors": {"*": f"{on} 以前沒有公布的條文"},
        }
        for q, d in missing
    ]
    rows.sort(key=lambda r: (r["qid"], r["doc"]))
    names = [j.name for j in judges]
    floor = int(qrels_doc["relevant_from"])
    judged = [r for r in rows if "grades" in r]
    doc = gs.write(
        args.out,
        {
            "kind": "judgements",
            "subset": args.subset,
            "golden_sha256": golden_sha,
            "qrels_sha256": qrels_doc["sha256"],
            "split_sha256": split_doc["sha256"],
            "runs": [
                {"path": p.name, "sha256": r["sha256"], "arm": r["arm"]}
                for p, r in zip(args.run, runs, strict=True)
            ],
            "depth": args.depth,
            "calibration": {"path": args.calibration.name, "sha256": calibration["sha256"]},
            "date": date.today().isoformat(),
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "as_of": on,
            "prompt": prompt_record(),
            **described,
            "relevant_from": floor,
            "rule": "最終分數取兩位判定者的較低者；任一位沒有分數則仍是洞。"
            f"題庫給定的條併入時取 max({gs.UPSTREAM_GRADE}, 分數)",
            "counts": {
                "pairs": len(pairs),
                "holes": len(holes),
                "upstream_ungraded": len(ungraded),
                "questions": len({q for q, _ in pairs}),
                "graded": sum(r["grade"] is not None for r in rows),
                "ungraded": sum(r["grade"] is None for r in rows),
                "no_text": len(missing),
                "final_grades": {str(g): sum(r["grade"] == g for r in rows) for g in gs.SCALE},
            },
            "agreement": agreement({n: [r["grades"][n] for r in judged] for n in names}, floor),
            "usage": _usage(judges),
            "rows": rows,
        },
    )
    print(
        json.dumps(
            {k: doc[k] for k in ("counts", "agreement", "usage")}, ensure_ascii=False, indent=1
        )
    )
    print(f"{args.out}：sha256 {doc['sha256']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="relevance_judge")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (
        ("calibrate", "在開發集判已知正例與同法規的隨機負例，量判定器的召回與誤判"),
        ("pool", "判一個子集各 run 前 10 條的洞與尚未分級的期望條文，寫封存的判定檔"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("golden", type=Path)
        p.add_argument("--qrels", type=Path, required=True)
        p.add_argument("--split", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument(
            "--cache", type=Path, required=True, help="倉庫外的 JSONL 快取（含題目全文）"
        )
        p.add_argument("--as-of", default=date.today().isoformat(), help="條文版本日（預設今天）")
        p.add_argument("--workers", type=int, default=8)
        p.add_argument(
            "--no-distinct-check", action="store_true", help="略過「別名指向同一模型」的檢查"
        )
        if name == "calibrate":
            p.add_argument("--seed", default="envcite-calibration-1")
        else:
            p.add_argument("--subset", choices=("dev", "test"), required=True)
            p.add_argument("--run", type=Path, action="append", required=True)
            p.add_argument("--calibration", type=Path, required=True)
            p.add_argument("--depth", type=int, default=gs.HOLE_DEPTH)
    args = parser.parse_args()
    {"calibrate": cmd_calibrate, "pool": cmd_pool}[args.command](args)


if __name__ == "__main__":
    main()
