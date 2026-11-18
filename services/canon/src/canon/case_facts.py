"""問句的案件事實：把問句裡明說的事實對到概念代碼，供 `Evaluate` 當輸入。

詞彙是某日有效、已確認的陳述所用的可陳述要件（旗標與數值；不含需判斷、承自上位、委由他處的）。
抽取者對候選概念逐一判斷問句是有、沒有、還是沒說（預設沒說），每個有或沒有附問句裡的原文片段；
程式再檢查概念在候選集、型別相符、原文片段與數字確實在問句裡，不過的丟掉，不補值。候選概念依問句
由嵌入檢索挑出，不依任何標準答案挑。量測見 `tools/fact_extraction_eval.py`。

模型的輸出不可信：只有通過檢查的事實出去；任何一步失敗都丟例外，呼叫端退回沒有事實的做法。
"""

import json
import math
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from typing import Literal, NamedTuple, Protocol

from canon.db import Conn, fetch
from canon.embedding import Embeds

type Kind = Literal["flag", "number"]
type Value = bool | float


@dataclass(frozen=True)
class Concept:
    """候選概念：抽取者唯一能選的詞彙。"""

    code: str
    name: str
    kind: Kind
    unit: str | None


class Cond(NamedTuple):
    """一個可陳述的要件：它所屬的陳述、概念、比較子與門檻，以及該陳述所在條文的文字。"""

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
       k.code, k.name,
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


def conditions(conn: Conn, as_of: date) -> list[Cond]:
    """某日有效且已確認的陳述的可陳述要件（不含需判斷、承自上位、委由他處的）。"""
    return fetch(conn, _CONDITIONS, (as_of, as_of), Cond)


def vocabulary(conds: list[Cond]) -> dict[str, Concept]:
    """候選概念；比較值為 1 的相等比較視為旗標，其餘為數值。"""
    out: dict[str, Concept] = {}
    for c in conds:
        flag = c.comparator == "eq" and c.threshold == 1.0 and c.unit is None
        out.setdefault(c.code, Concept(c.code, c.name, "flag" if flag else "number", c.unit))
    return out


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
    """一項抽出的事實：概念代碼、值（旗標或數字）與問句裡的原文片段。"""

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


class Completes(Protocol):
    """抽取用的模型：只要會依提示回一段文字。"""

    def complete(self, prompt: str) -> str:
        """依提示回一段文字。"""
        ...


class Unavailable(RuntimeError):
    """沒有嵌入端點可用，而詞彙大到必須檢索候選。"""


class Extraction(NamedTuple):
    """一次抽取的結果：通過檢查的事實、事實所屬概念的名稱，與給模型看過的候選概念數。"""

    facts: list[Fact]
    names: dict[str, str]
    candidates: int


#: 取一條唯讀連線的方法；抽取只在詞彙過期、要重讀時才借，模型呼叫期間不占連線。
type Connect = Callable[[], AbstractContextManager[Conn]]


#: 給模型看的候選概念數，依問句由嵌入檢索。
CANDIDATES = 12
#: 詞彙（與概念名稱的向量）重算的間隔（秒）：已確認的陳述不常變。
VOCABULARY_TTL = 300.0


@dataclass(frozen=True)
class _Vocabulary:
    concepts: dict[str, Concept]
    vectors: dict[str, list[float]]
    expires: float


class CaseFactExtractor:
    """從問句抽案件事實。執行緒安全：詞彙以鎖保護，其餘無共用狀態。"""

    def __init__(
        self,
        model: Completes,
        embedder: Embeds | None,
        *,
        candidates: int = CANDIDATES,
        ttl: float = VOCABULARY_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._embedder = embedder
        self._candidates = candidates
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: _Vocabulary | None = None

    def extract(self, connect: Connect, question: str, as_of: date) -> Extraction:
        """問句裡明說的事實。詞彙是空的（沒有已確認的陳述）就沒有事實，不呼叫模型。"""
        known = self._vocabulary(connect, as_of)
        if not known.concepts:
            return Extraction([], {}, 0)
        shown = self._shown(known, question)
        facts = parse_facts(self._model.complete(extract_prompt(question, shown)))
        kept, _ = check(question, facts, shown)
        return Extraction(kept, {f.concept: shown[f.concept].name for f in kept}, len(shown))

    def _shown(self, known: _Vocabulary, question: str) -> dict[str, Concept]:
        """給模型看的候選：詞彙不大就全給，否則依問句檢索最相近的。"""
        if len(known.concepts) <= self._candidates:
            return known.concepts
        if self._embedder is None:
            raise Unavailable("the vocabulary is too large to offer whole and no embedder is set")
        query = self._embedder.embed([question])[0]
        near = nearest(known.concepts, known.vectors, query, self._candidates)
        return {c.code: c for c in near}

    def _vocabulary(self, connect: Connect, as_of: date) -> _Vocabulary:
        with self._lock:
            now = self._clock()
            if self._cached is not None and now < self._cached.expires:
                return self._cached
            with connect() as conn:
                concepts = vocabulary(conditions(conn, as_of))
            vectors: dict[str, list[float]] = {}
            if self._embedder is not None and len(concepts) > self._candidates:
                names = self._embedder.embed([c.name for c in concepts.values()])
                vectors = dict(zip(concepts, names, strict=True))
            self._cached = _Vocabulary(concepts, vectors, now + self._ttl)
            return self._cached
