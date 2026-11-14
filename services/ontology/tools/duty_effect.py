"""義務兩型瑕疵修復（MR 339）效果的獨立量測：盲測、雙模型判定池、輪間範圍檢定。

    # 一、收集：每個執行（一個庫）已確認的義務與禁止陳述，去掉條件標記後匿名、打亂
    uv run python tools/duty_effect.py collect --dsn 'postgresql://…/{db}' \\
        --run pre-1=pre_1 --run pre-2=pre_2 --run pre-3=pre_3 \\
        --run post-1=post_1 --run post-2=post_2 --run post-3=post_3 \\
        --sample-n 30 --seed 20261109 --items items.json --index index.json
    # 二、判定：只讀 items.json（沒有條件、執行、輪號），兩位判定者各回答固定的窄問題
    uv run python tools/duty_effect.py judge items.json --cache judge-cache.jsonl \\
        --out judgements.json
    # 三、彙整：這一步才把匿名編號對回條件
    uv run python tools/duty_effect.py report index.json judgements.json --out eval/<檔名>.json
    # 四、未明示的主體實際由誰負擔（輸入是 duty_cost.py unstated 的輸出）
    uv run python tools/duty_effect.py bearer unstated.json \\
        --cache bearer-cache.jsonl --out bearer.json

判定者取自 `--models`（預設 `eval/duty-fix-effect-judges.toml`，格式同 `models.toml` 的
`[relevance]`），彼此不同家族、都與被測的抽取者不同家族；判定前以 `SameModel` 探測別名。
判定者看到的標註，對兩個條件用同一個函式轉成文字：不標「承自上位」，主體空白一律寫成同一句，
條件、執行與輪號都不在 items.json。

瑕疵是兩型：
- 主體：寫成被規範的物、原文沒有的字、不是義務人，或空白而原文其實寫出了義務人。
- 適用範圍：漏了引文所在句的條件或上位導言的條件，或引文停在並列的行為之前。
判定者指出的缺漏必須是原文的逐字片段，找不到的不算瑕疵（另計為「無依據」），所以判定者編不出缺漏。

這個工具不寫資料庫，也不呼叫 `duty_defects.py` 的規則：那支量的是寫入前的規則擋下多少，
與修復同一份程式。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import threading
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from envcite_ontology.verification.config import load_judges
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, ModelError

DUTY = "s.statement_kind = 'prescriptive' AND s.norm_modality IN ('obligation', 'prohibition')"

#: 主體空白時給判定者的字：兩個條件相同，判定者不知道它來自哪個版本。
BLANK_SUBJECT = "（空白，未填）"

SUBJECT_CHOICES = {
    "actor": "主體是原文寫出的、負有這個義務的人、組織，或法律明文列舉為受規範者的事業、系統、設施"
    "（例如「事業、污水下水道系統或建築物污水處理設施，排放廢水者，應…」裡的每一個）",
    "unstated_ok": "主體欄空白，而原文（含上層條文）確實沒有寫出義務人",
    "thing": "主體寫成被規範的物、設施、物質、水或行為，而原文並不是把它列為受規範者"
    "（例如「飲用水設備應符合標準」的「飲用水設備」、「…之建造、管理及清理」）",
    "invented": "主體是原文沒有的字（例如補上「任何人」「行為人」）",
    "missed_actor": "主體欄空白，但原文（含上層條文）其實寫出了義務人",
    "wrong_actor": "主體是人或組織，但不是這個義務的義務人",
}
#: 判定者選了就算主體瑕疵的選項（`missed_actor` 另要原文依據）。
SUBJECT_DEFECT = frozenset({"thing", "invented", "wrong_actor"})

PROMPT = """你是法規標註的獨立稽核員。下面是一個條文單元的原文（含上層條文）、一段引文，與依該引文
產生的義務標註。你不知道這份標註出自哪個系統。只回答下列三個問題，只輸出 JSON。

問題一，主體：標註的「主體」欄屬於下列哪一種？
{choices}

問題二，適用範圍：引文所在的同一句（含上層條文的導言，例如款之上的項）裡，有沒有改變這個義務適用範圍
的條件，沒有出現在標註的「適用範圍」裡？條件包括引文之前同一句的條件子句、上層導言的條件（例如
「在水污染管制區內」）。別的句子、別項的條件不算；標註已用其他字句涵蓋的也不算缺。缺少時，
「missing_text」要逐字抄原文的那一段。

問題三，截斷：引文有沒有停在並列的行為或替代方式之前（同一句後面還有「或…」「並…」，而引文與標註的
義務內容都沒有包含），使義務變窄？有的話，「truncated_text」要逐字抄原文被漏掉的那一段。

輸出：{{"subject": "上列選項的代號",
 "actor_text": "選 missed_actor 時，原文寫出的義務人，逐字；否則空字串",
 "missing_condition": true 或 false, "missing_text": "…", "truncated": true 或 false,
 "truncated_text": "…"}}

{context}單元原文（{unit}）：
{text}

標註對應的引文：
{quote}

標註：
{annotation}
"""

# 資料庫


def context_of(conn: Any, provision_id: int) -> str:
    """上層條文的現行文字，由外到內。"""
    return "\n".join(
        str(r[0])
        for r in conn.execute(
            "WITH RECURSIVE up(id, parent_id, depth) AS ("
            " SELECT p.id, p.parent_id, 0 FROM corpus.provision p WHERE p.id = %s"
            " UNION ALL SELECT p.id, p.parent_id, up.depth + 1"
            "   FROM corpus.provision p JOIN up ON p.id = up.parent_id)"
            " SELECT DISTINCT ON (up.depth) v.content FROM up"
            "  JOIN corpus.provision_version v ON v.provision_id = up.id"
            "   AND v.superseded_at IS NULL"
            " WHERE up.depth > 0 AND v.content IS NOT NULL AND btrim(v.content) <> ''"
            " ORDER BY up.depth DESC, v.id DESC",
            (provision_id,),
        )
    )


def _has_column(conn: Any, table: str, column: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'annotation'"
            " AND table_name = %s AND column_name = %s",
            (table, column),
        ).fetchone()
        is not None
    )


def fetch_confirmed(conn: Any, version: str) -> list[dict[str, Any]]:
    """一個抽取版本已確認的義務與禁止陳述，連同給判定者看的標註。

    修復前的綱要沒有 `unstated`、`carried` 欄，那兩個旗標一律是假；判定者看到的文字不含旗標。
    """
    unstated = "sl.unstated" if _has_column(conn, "statement_slot", "unstated") else "false"
    carried = "c.carried" if _has_column(conn, "statement_condition", "carried") else "false"
    rows = conn.execute(
        "SELECT s.id, s.provision_id, r.local_id, p.path, v.content, lower(s.span), upper(s.span),"
        "       s.norm_modality::text,"
        "       (SELECT x.round FROM annotation.extraction_attempt x"
        "         WHERE x.provision_id = s.provision_id AND x.content_sha256 = s.content_sha256"
        "           AND x.producer_version = d.producer_version AND x.attempted_at >= d.run_at"
        "         ORDER BY x.attempted_at LIMIT 1)"
        "  FROM annotation.statement s JOIN annotation.assertion a ON a.id = s.id"
        "  JOIN annotation.derivation d ON d.id = a.derivation_id"
        "  JOIN corpus.provision p ON p.id = s.provision_id"
        "  JOIN corpus.legal_resource r ON r.id = p.resource_id"
        "  JOIN corpus.provision_version v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256"
        f" WHERE {DUTY} AND a.confirm_state = 'confirmed' AND d.producer_version = %s"
        " ORDER BY s.id",
        (version,),
    ).fetchall()
    contexts: dict[int, str] = {}
    out: list[dict[str, Any]] = []
    for sid, pid, law, path, content, lo, hi, modality, rnd in rows:
        subjects = [
            (str(name) if name is not None else "", bool(unst))
            for name, unst in conn.execute(
                f"SELECT coalesce(k.name, sl.literal), {unstated} FROM annotation.statement_slot sl"
                " LEFT JOIN vocab.concept k ON k.id = sl.concept_id"
                " WHERE sl.statement_id = %s AND sl.slot_role = 'subject' ORDER BY sl.alternative",
                (sid,),
            )
        ]
        conds = [
            (str(role), str(name), bool(neg), bool(carr))
            for role, name, neg, carr in conn.execute(
                "SELECT c.condition_role::text, k.name, c.negated, "
                + carried
                + " FROM annotation.statement_condition c"
                " JOIN vocab.concept k ON k.id = c.concept_id"
                " WHERE c.statement_id = %s ORDER BY c.condition_role, c.disjunct_no, c.id",
                (sid,),
            )
        ]
        text = str(content)
        contexts.setdefault(int(pid), context_of(conn, int(pid)))
        out.append(
            {
                "id": int(sid),
                "provision_id": int(pid),
                "unit": f"{law} {path}",
                "text": text,
                "context": contexts[int(pid)],
                "span": [0, len(text)] if lo is None else [int(lo), int(hi)],
                "quote": text if lo is None else text[lo:hi],
                "round": int(rnd or 0),
                "annotation": render_annotation(str(modality), subjects, conds),
                "unstated": any(u for _, u in subjects),
                "carried": sum(1 for *_, c in conds if c),
                "parts": {"modality": str(modality), "subjects": subjects, "conditions": conds},
            }
        )
    return out


# 給判定者看的文字與匿名編號


def render_annotation(
    modality: str,
    subjects: Sequence[tuple[str, bool]],
    conditions: Sequence[tuple[str, str, bool, bool]],
) -> str:
    """標註轉成判定者看的文字。對兩個條件同一個函式：不寫「承自上位」，空白主體一律同一句。"""
    by_role: dict[str, list[str]] = defaultdict(list)
    for role, name, negated, _carried in conditions:
        by_role[role].append(("非：" if negated else "") + name)
    shown = "；".join(BLANK_SUBJECT if unstated else name for name, unstated in subjects)
    lines = [
        f"義務方向：{'義務' if modality == 'obligation' else '禁止'}",
        f"主體：{shown or '（沒有主體欄）'}",
        f"適用範圍（皆須成立）：{'；'.join(by_role['scope']) or '（無）'}",
        f"義務內容：{'；'.join(by_role['duty']) or '（無）'}",
    ]
    if by_role["limit"]:
        lines.append(f"限值：{'；'.join(by_role['limit'])}")
    return "\n".join(lines)


def blind_key(version: str, statement_id: int) -> str:
    """匿名編號：不含版本、庫或輪號的雜湊，只有 index.json 能對回去。"""
    return hashlib.sha256(f"{version}:{statement_id}".encode()).hexdigest()[:12]


def segments_of(spans: Sequence[tuple[int, int]]) -> list[int]:
    """同一單元內，重疊的引文區間算同一段；回傳與輸入同序的段號。"""
    order = sorted(range(len(spans)), key=lambda i: spans[i])
    seg = [0] * len(spans)
    current, end = -1, -1
    for i in order:
        lo, hi = spans[i]
        if current < 0 or lo >= end:
            current += 1
            end = hi
        else:
            end = max(end, hi)
        seg[i] = current
    return seg


def pick(ids: Iterable[int], n: int, seed: int, version: str) -> set[int]:
    """每個執行固定抽 n 筆（不足就全取）；種子與版本名決定，與資料庫的列序無關。"""
    pool = sorted(ids)
    rng = random.Random(f"{seed}:{version}")
    return set(rng.sample(pool, min(n, len(pool))))


def mutations(record: Mapping[str, Any]) -> dict[str, str]:
    """對照組：把一筆標註改壞成已知的瑕疵，判定者應該判出來。種類 → 改壞後的標註文字。

    - `drop_carried`：拿掉程式帶入的上位適用情形，標註就缺了引文所在句或上位導言的條件。
    - `subject_as_action`：把空白的主體換成義務內容（一個行為），主體就成了被規範的行為。
    """
    parts = record["parts"]
    out: dict[str, str] = {}
    conditions = parts["conditions"]
    if any(c[3] for c in conditions):
        kept = [c for c in conditions if not c[3]]
        out["drop_carried"] = render_annotation(parts["modality"], parts["subjects"], kept)
    duty = next((c[1] for c in conditions if c[0] == "duty"), None)
    if record["unstated"] and duty:
        out["subject_as_action"] = render_annotation(parts["modality"], [(duty, False)], conditions)
    return out


def build(
    runs: Mapping[str, Sequence[Mapping[str, Any]]],
    sample_n: int,
    seed: int,
    controls: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """（給判定者的項目，匿名編號 → 來源）。項目依種子打亂。

    `controls` 時，抽樣內有可改壞的陳述（見 `mutations`）另加對照項目，匿名編號與一般項目同形。
    """
    items: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for version, records in runs.items():
        chosen = pick((int(r["id"]) for r in records), sample_n, seed, version)
        by_unit: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for r in records:
            by_unit[int(r["provision_id"])].append(r)
        for group in by_unit.values():
            segs = segments_of([(int(r["span"][0]), int(r["span"][1])) for r in group])
            for r, seg in zip(group, segs, strict=True):
                key = blind_key(version, int(r["id"]))
                items.append(
                    {k: r[k] for k in ("unit", "text", "context", "quote", "annotation")}
                    | {"key": key}
                )
                index[key] = {
                    "run": version,
                    "condition": version.rsplit("-", 1)[0],
                    "id": int(r["id"]),
                    "unit": r["unit"],
                    "round": int(r["round"]),
                    "segment": f"{version}|{r['provision_id']}|{seg}",
                    "sampled": int(r["id"]) in chosen,
                    "unstated": bool(r["unstated"]),
                    "carried": int(r["carried"]),
                }
                if not (controls and int(r["id"]) in chosen):
                    continue
                for kind, annotation in mutations(r).items():
                    ckey = blind_key(f"{kind}:{version}", int(r["id"]))
                    items.append(
                        {k: r[k] for k in ("unit", "text", "context", "quote")}
                        | {"key": ckey, "annotation": annotation}
                    )
                    index[ckey] = {"control": kind, "of": key, "run": version}
    random.Random(seed).shuffle(items)
    return items, index


# 判定

_PUNCT = re.compile(r"[\s，、。；：「」（）()．,.;:·‧…⋯]")
_VARIANTS = str.maketrans({"汙": "污", "巿": "市"})


def squash(text: str) -> str:
    return _PUNCT.sub("", text).translate(_VARIANTS)


def _squashed(text: str) -> tuple[str, list[int]]:
    """去掉標點與空白、異體字歸一後的字串，與每個字在原字串的位置。"""
    chars: list[str] = []
    at: list[int] = []
    for i, c in enumerate(text):
        if _PUNCT.fullmatch(c):
            continue
        chars.append(c.translate(_VARIANTS))
        at.append(i)
    return "".join(chars), at


def locate(evidence: str, haystack: str) -> tuple[int, int] | None:
    """判定者引的原文片段在 `haystack` 的位置（起，迄）。

    「…」把引文分成幾段，每段（兩字以上）依序都要找得到，而且至少一段有四字以上；找不到是 None。
    """
    pieces = [squash(p) for p in re.split(r"[…⋯]+|\.{3,}", evidence)]
    pieces = [p for p in pieces if len(p) >= 2]
    if not pieces or not any(len(p) >= 4 for p in pieces):
        return None
    flat, at = _squashed(haystack)
    pos, first, last = 0, -1, -1
    for piece in pieces:
        k = flat.find(piece, pos)
        if k < 0:
            return None
        first = k if first < 0 else first
        pos = k + len(piece)
        last = pos - 1
    return at[first], at[last] + 1


def supported(evidence: str, *texts: str) -> bool:
    """判定者引的原文片段確實在原文裡。"""
    return locate(evidence, "\n".join(texts)) is not None


#: 句界：條件子句與它管到的引文之間不能隔著句界。
_SENTENCE_END = "。；：:"
#: 判定者指出的缺漏，至少這個比例的字已落在標註裡的三字以上共同片段，就算標註已涵蓋。
_COVERED_SHARE = 0.8


def covered(evidence: str, annotation: str) -> bool:
    """標註的主體、適用範圍與義務內容已經寫了這段缺漏。"""
    body = "\n".join(
        line.split("：", 1)[1]
        for line in annotation.splitlines()
        if "：" in line and not line.startswith("義務方向")
    )
    ev, ann = squash(evidence), squash(body)
    if len(ev) < 3:
        return False
    hit = [False] * len(ev)
    for i in range(len(ev) - 2):
        if ev[i : i + 3] in ann:
            hit[i : i + 3] = [True, True, True]
    return sum(hit) / len(ev) >= _COVERED_SHARE


def _quote_at(text: str, quote: str) -> int:
    """引文在單元原文的起點（異體字歸一後找）；找不到是 -1。"""
    return text.translate(_VARIANTS).find(quote.translate(_VARIANTS))


def missing_holds(evidence: str, text: str, context: str, quote: str, annotation: str) -> bool:
    """引的缺漏確是引文之前同一句的條件，或上層導言的條件，而且標註沒寫。"""
    if locate(evidence, context) is None:
        span = locate(evidence, text)
        start = _quote_at(text, quote) if quote else -1
        if span is None:
            return False
        if (
            quote
            and start >= 0
            and (span[1] > start or any(b in text[span[1] : start] for b in _SENTENCE_END))
        ):
            return False
    return not (annotation and covered(evidence, annotation))


def truncation_holds(evidence: str, text: str, quote: str, annotation: str) -> bool:
    """引的缺漏確是引文之後同一句的並列行為或替代方式，而且標註沒寫。"""
    span = locate(evidence, text)
    if span is None:
        return False
    start = _quote_at(text, quote) if quote else -1
    if quote and start >= 0:
        end = start + len(quote)
        if span[0] < end or any(b in text[end : span[0]] for b in _SENTENCE_END):
            return False
    return not (annotation and covered(evidence, annotation))


def normalize(answer: Mapping[str, Any]) -> dict[str, Any]:
    subject = str(answer.get("subject", ""))
    return {
        "subject": subject if subject in SUBJECT_CHOICES else "unparsed",
        "actor_text": str(answer.get("actor_text") or ""),
        "missing_condition": answer.get("missing_condition") is True,
        "missing_text": str(answer.get("missing_text") or ""),
        "truncated": answer.get("truncated") is True,
        "truncated_text": str(answer.get("truncated_text") or ""),
    }


def flags(
    answer: Mapping[str, Any],
    text: str,
    context: str,
    quote: str = "",
    annotation: str = "",
) -> dict[str, bool]:
    """一位判定者的答案算成瑕疵旗標。

    缺漏要有原文依據：逐字在原文裡，而且位置合乎問題的定義（引文之前同一句或上層導言；引文之後同一句），
    標註也沒寫。不合的另記 `unsupported`，不算瑕疵。沒給引文時不查位置與涵蓋。
    """
    subject = answer["subject"] in SUBJECT_DEFECT
    missed = answer["subject"] == "missed_actor"
    missed_ok = missed and supported(answer["actor_text"], text, context)
    missing = bool(answer["missing_condition"])
    missing_ok = missing and missing_holds(answer["missing_text"], text, context, quote, annotation)
    truncated = bool(answer["truncated"])
    truncated_ok = truncated and truncation_holds(answer["truncated_text"], text, quote, annotation)
    return {
        "subject": subject or missed_ok,
        "scope": missing_ok or truncated_ok,
        "unsupported": (missed and not missed_ok)
        or (missing and not missing_ok)
        or (truncated and not truncated_ok),
        "unparsed": answer["subject"] == "unparsed",
    }


def prompt_of(item: Mapping[str, Any]) -> str:
    context = f"上層條文：\n{item['context']}\n\n" if item["context"] else ""
    choices = "\n".join(f"- {k}：{v}" for k, v in SUBJECT_CHOICES.items())
    return PROMPT.format(
        choices=choices,
        context=context,
        unit=item["unit"],
        text=item["text"],
        quote=item["quote"],
        annotation=item["annotation"],
    )


def prompt_sha() -> str:
    return hashlib.sha256(PROMPT.encode()).hexdigest()


class Cache:
    """追加寫入的 JSONL：中斷後重跑不重花費用。鍵是（判定者、完整提示）的雜湊。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._data[row["key"]] = row["answer"]

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def put(self, key: str, answer: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = answer
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "answer": answer}, ensure_ascii=False) + "\n")


#: 判定者答不出可解析的 JSON 時再問的次數（溫度 0，同樣的提示通常同樣失敗，所以只補問一次）。
RETRIES = 1


def ask(judge: Model, item: Mapping[str, Any], cache: Cache) -> dict[str, Any]:
    prompt = prompt_of(item)
    key = hashlib.sha256(f"{judge.name}\n{prompt}".encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        return hit
    answer: dict[str, Any] | None = None
    for _ in range(RETRIES + 1):
        try:
            answer = object_from(judge.complete(prompt))
        except ModelError:
            answer = None
        if answer is not None:
            break
    out = normalize(answer or {})
    if answer is not None:  # 模型沒答的不入快取，下次再試
        cache.put(key, out)
    return out


def judge_all(
    items: Sequence[Mapping[str, Any]], judges: Sequence[Model], cache: Cache, workers: int
) -> list[dict[str, Any]]:
    jobs = [(it, j) for it in items for j in judges]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(lambda ij: ask(ij[1], ij[0], cache), jobs))
    rows: dict[str, dict[str, Any]] = {}
    for (item, judge), answer in zip(jobs, answers, strict=True):
        row = rows.setdefault(
            str(item["key"]),
            {
                "key": item["key"],
                "text": item["text"],
                "context": item["context"],
                "quote": item["quote"],
                "annotation": item["annotation"],
                "answers": {},
            },
        )
        row["answers"][judge.name] = answer
    return list(rows.values())


# 彙整

DEFECTS = ("subject", "scope")

#: 先前抽查（eval/duty-reextraction-2026-11-06.md）判為實質瑕疵的單元。這些單元的瑕疵在修復前就有，
#: 逐單元看修復前後各有幾筆已確認的陳述、被判有瑕疵幾筆。
KNOWN_DEFECT_UNITS = (
    "O0040001 art_25/para_2",
    "O0040010 art_13/para_1",
    "O0040001 art_10/para_2",
    "O0040001 art_10/para_5",
    "O0040001 art_30/para_1/pt_1",
    "O0040001 art_30/para_1/pt_3",
    "O0040001 art_18-1/para_1",
    "O0040001 art_18-1/para_2",
)


def kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Cohen's κ（二元）；沒有資料或兩位都全判同一類時沒有定義。"""
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def verdict(row: Mapping[str, Any], judge_names: Sequence[str]) -> dict[str, dict[str, bool]]:
    """每種判定規則下，這個陳述有沒有瑕疵。規則：兩位都判、任一位判、只看第一位（管線外的家族）。"""
    per = {
        n: flags(
            row["answers"][n],
            row["text"],
            row["context"],
            row.get("quote", ""),
            row.get("annotation", ""),
        )
        for n in judge_names
    }
    out: dict[str, dict[str, bool]] = {"both": {}, "any": {}, "first": {}}
    for kind in (*DEFECTS, "any_defect"):
        values = [
            (f["subject"] or f["scope"]) if kind == "any_defect" else f[kind] for f in per.values()
        ]
        out["both"][kind] = all(values)
        out["any"][kind] = any(values)
        out["first"][kind] = values[0]
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """二項比例的 Wilson 95% 區間。同一段引文在不同輪重複確認，陳述不互相獨立，所以只描述不檢定。"""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def pooled(table: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """各條件三輪合併的瑕疵率與 Wilson 區間（兩位都判、任一位判）。"""
    out: dict[str, dict[str, Any]] = {}
    for run in table.values():
        cell = out.setdefault(run["condition"], {"n": 0, "both": 0, "any": 0})
        cell["n"] += run["n"]
        cell["both"] += run["defects"]["both"].get("any_defect", 0)
        cell["any"] += run["defects"]["any"].get("any_defect", 0)
    for cell in out.values():
        for rule in ("both", "any"):
            cell[f"{rule}_rate"] = round(cell[rule] / cell["n"], 3) if cell["n"] else None
            cell[f"{rule}_wilson"] = wilson(cell[rule], cell["n"])
    return out


def spread(values: Sequence[float]) -> float:
    return max(values) - min(values)


def compare(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """兩組各輪的值：兩組無重疊且平均差大於兩組中較大的輪間範圍，才算有差。

    三輪對三輪完全分離的單尾精確置換 p = 0.05，是這個樣本數的下限。
    """
    diff = statistics.fmean(b) - statistics.fmean(a)
    separated = max(a) < min(b) or max(b) < min(a)
    wider = max(spread(a), spread(b))
    return {
        "diff": round(diff, 3),
        "range": round(wider, 3),
        "separated": separated,
        "verdict": "有差" if separated and abs(diff) > wider else "不能區分",
    }


def run_table(
    rows: Sequence[Mapping[str, Any]],
    index: Mapping[str, Mapping[str, Any]],
    judge_names: Sequence[str],
    *,
    sampled_only: bool,
    round0_only: bool,
) -> dict[str, dict[str, Any]]:
    """每個執行的計數。`clean` 是至少有一個無瑕疵陳述的引文段（兩位判定者都沒判瑕疵的）。"""
    runs: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = index[row["key"]]
        if "control" in meta:
            continue
        if (sampled_only and not meta["sampled"]) or (round0_only and meta["round"] != 0):
            continue
        run = runs.setdefault(
            meta["run"],
            {
                "condition": meta["condition"],
                "n": 0,
                "unstated": 0,
                "segments": set(),
                "clean_segments": {"any": set(), "both": set()},
                "defects": {rule: defaultdict(int) for rule in ("both", "any", "first")},
                "unsupported": 0,
                "unparsed": 0,
            },
        )
        run["n"] += 1
        run["unstated"] += meta["unstated"]
        run["segments"].add(meta["segment"])
        v = verdict(row, judge_names)
        for rule, kinds in v.items():
            for kind, hit in kinds.items():
                run["defects"][rule][kind] += hit
        # 「乾淨」：兩位都沒判瑕疵（嚴）；不是兩位都判瑕疵（寬）
        if not v["any"]["any_defect"]:
            run["clean_segments"]["any"].add(meta["segment"])
        if not v["both"]["any_defect"]:
            run["clean_segments"]["both"].add(meta["segment"])
        flagged = [
            flags(
                row["answers"][n],
                row["text"],
                row["context"],
                row.get("quote", ""),
                row.get("annotation", ""),
            )
            for n in judge_names
        ]
        run["unsupported"] += any(f["unsupported"] for f in flagged)
        run["unparsed"] += any(f["unparsed"] for f in flagged)
    return {
        name: {
            "condition": r["condition"],
            "n": r["n"],
            "unstated": r["unstated"],
            "segments": len(r["segments"]),
            "clean_segments_strict": len(r["clean_segments"]["any"]),
            "clean_segments_lenient": len(r["clean_segments"]["both"]),
            "defects": {rule: dict(d) for rule, d in r["defects"].items()},
            "unsupported": r["unsupported"],
            "unparsed": r["unparsed"],
        }
        for name, r in sorted(runs.items())
    }


def metrics(table: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, list[float]]]:
    """條件 → 指標 → 各輪的值。"""
    out: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for run in table.values():
        m = out[run["condition"]]
        n = max(run["n"], 1)
        m["confirmed"].append(run["n"])
        m["segments"].append(run["segments"])
        m["clean_segments_strict"].append(run["clean_segments_strict"])
        m["clean_segments_lenient"].append(run["clean_segments_lenient"])
        for rule in ("both", "any", "first"):
            for kind in (*DEFECTS, "any_defect"):
                count = run["defects"][rule].get(kind, 0)
                m[f"{kind}_{rule}_count"].append(count)
                m[f"{kind}_{rule}_rate"].append(round(count / n, 4))
    return {c: dict(m) for c, m in out.items()}


def comparisons(m: Mapping[str, Mapping[str, Sequence[float]]], a: str, b: str) -> dict[str, Any]:
    return {k: compare(m[a][k], m[b][k]) for k in m[a] if k in m[b]}


def judge_agreement(
    rows: Sequence[Mapping[str, Any]], judge_names: Sequence[str]
) -> dict[str, Any]:
    """兩位判定者的一致性：二元的瑕疵旗標用 κ，六選一的主體用 κ 與一致率。"""
    first, second = judge_names[0], judge_names[1]
    out: dict[str, Any] = {"judges": [first, second], "n": len(rows)}
    per = [
        (
            flags(
                r["answers"][first],
                r["text"],
                r["context"],
                r.get("quote", ""),
                r.get("annotation", ""),
            ),
            flags(
                r["answers"][second],
                r["text"],
                r["context"],
                r.get("quote", ""),
                r.get("annotation", ""),
            ),
        )
        for r in rows
    ]
    for kind in DEFECTS:
        a, b = [x[kind] for x, _ in per], [y[kind] for _, y in per]
        k = kappa(a, b)
        out[kind] = {
            "kappa": None if k is None else round(k, 3),
            "agree": round(sum(x == y for x, y in zip(a, b, strict=True)) / max(len(a), 1), 3),
            "first_rate": round(sum(a) / max(len(a), 1), 3),
            "second_rate": round(sum(b) / max(len(b), 1), 3),
        }
    a_any = [x["subject"] or x["scope"] for x, _ in per]
    b_any = [y["subject"] or y["scope"] for _, y in per]
    k_any = kappa(a_any, b_any)
    out["any_defect"] = {
        "kappa": None if k_any is None else round(k_any, 3),
        "agree": round(
            sum(x == y for x, y in zip(a_any, b_any, strict=True)) / max(len(a_any), 1), 3
        ),
    }
    labels = [(r["answers"][first]["subject"], r["answers"][second]["subject"]) for r in rows]
    po = sum(x == y for x, y in labels) / max(len(labels), 1)
    cats = sorted({c for pair in labels for c in pair})
    pe = sum(
        (sum(x == c for x, _ in labels) / max(len(labels), 1))
        * (sum(y == c for _, y in labels) / max(len(labels), 1))
        for c in cats
    )
    out["subject_category"] = {
        "kappa": None if pe == 1 else round((po - pe) / (1 - pe), 3),
        "agree": round(po, 3),
        "confusion": {
            f"{x}|{y}": sum(p == (x, y) for p in labels)
            for x in cats
            for y in cats
            if any(p == (x, y) for p in labels)
        },
    }
    out["unsupported"] = sum(any(f["unsupported"] for f in pair) for pair in per)
    out["unparsed"] = sum(any(f["unparsed"] for f in pair) for pair in per)
    return out


def known_units_report(
    rows: Sequence[Mapping[str, Any]],
    index: Mapping[str, Mapping[str, Any]],
    judge_names: Sequence[str],
) -> dict[str, Any]:
    """先前抽查找到的瑕疵單元：各條件（三輪合計）已確認幾筆、被判瑕疵幾筆、幾輪至少有一筆乾淨的。"""
    out: dict[str, dict[str, Any]] = {}
    for unit in KNOWN_DEFECT_UNITS:
        per: dict[str, dict[str, Any]] = {}
        for row in rows:
            meta = index[row["key"]]
            if "control" in meta or meta.get("unit") != unit:
                continue
            cell = per.setdefault(
                meta["condition"],
                {"confirmed": 0, "defect_both": 0, "defect_any": 0, "runs_with_clean": set()},
            )
            v = verdict(row, judge_names)
            cell["confirmed"] += 1
            cell["defect_both"] += v["both"]["any_defect"]
            cell["defect_any"] += v["any"]["any_defect"]
            if not v["any"]["any_defect"]:
                cell["runs_with_clean"].add(meta["run"])
        out[unit] = {
            cond: {**c, "runs_with_clean": len(c["runs_with_clean"])}
            for cond, c in sorted(per.items())
        }
    return out


def unstated_audit(
    rows: Sequence[Mapping[str, Any]],
    index: Mapping[str, Mapping[str, Any]],
    judge_names: Sequence[str],
) -> dict[str, Any]:
    """主體記成「未明示」的已確認陳述，判定者怎麼看：原文確實沒寫義務人，還是其實寫了。

    `right` 兩位都選 `unstated_ok`；`wrong` 兩位都選 `missed_actor`（義務人的逐字依據在原文）；
    其餘是兩位意見不同或沒有依據。每個條件各自計數（修復前不會有未明示）。
    """
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        meta = index[row["key"]]
        if "control" in meta or not meta.get("unstated"):
            continue
        kinds = [row["answers"][n]["subject"] for n in judge_names]
        cell = out.setdefault(meta["condition"], {"n": 0, "right": 0, "wrong": 0, "disputed": 0})
        cell["n"] += 1
        verdicts = [
            "missed_actor"
            if k == "missed_actor"
            and flags(
                row["answers"][n],
                row["text"],
                row["context"],
                row.get("quote", ""),
                row.get("annotation", ""),
            )["subject"]
            else k
            for k, n in zip(kinds, judge_names, strict=True)
        ]
        if all(v == "unstated_ok" for v in verdicts):
            cell["right"] += 1
        elif all(v == "missed_actor" for v in verdicts):
            cell["wrong"] += 1
        else:
            cell["disputed"] += 1
    return out


def controls_report(
    rows: Sequence[Mapping[str, Any]],
    index: Mapping[str, Mapping[str, Any]],
    judge_names: Sequence[str],
) -> dict[str, Any]:
    """對照組：改壞的標註被判出幾筆（召回），沒改壞的原標註被判出幾筆（誤判）。

    三種規則同 `verdict`：兩位都判、任一位判、只看第一位。
    """
    by_key = {r["key"]: r for r in rows}
    target = {"drop_carried": "scope", "subject_as_action": "subject"}
    out: dict[str, Any] = {}
    for kind, defect in target.items():
        pairs = [
            (by_key[k], by_key[m["of"]])
            for k, m in index.items()
            if m.get("control") == kind and k in by_key and m["of"] in by_key
        ]
        if not pairs:
            continue
        entry: dict[str, Any] = {"n": len(pairs), "defect": defect}
        for rule in ("both", "any", "first"):
            entry[rule] = {
                "mutated_flagged": sum(verdict(m, judge_names)[rule][defect] for m, _ in pairs),
                "original_flagged": sum(verdict(o, judge_names)[rule][defect] for _, o in pairs),
            }
        out[kind] = entry
    return out


def report_dict(
    index: Mapping[str, Mapping[str, Any]],
    judgements: Mapping[str, Any],
    base: str = "pre",
    treated: str = "post",
) -> dict[str, Any]:
    rows = judgements["rows"]
    names = list(judgements["judges"])
    result: dict[str, Any] = {
        "judges": names,
        "prompt_sha256": judgements["prompt_sha256"],
        "agreement": judge_agreement([r for r in rows if "control" not in index[r["key"]]], names),
        "controls": controls_report(rows, index, names),
        "known_units": known_units_report(rows, index, names),
        "unstated_audit": unstated_audit(rows, index, names),
        "slices": {},
    }
    for label, sampled, r0 in (
        ("sample_all_rounds", True, False),
        ("sample_round0", True, True),
        ("census_all_rounds", False, False),
        ("census_round0", False, True),
    ):
        table = run_table(rows, index, names, sampled_only=sampled, round0_only=r0)
        m = metrics(table)
        result["slices"][label] = {
            "per_run": table,
            "pooled": pooled(table),
            "metrics": m,
            "compare": comparisons(m, base, treated) if base in m and treated in m else {},
        }
    return result


# 未明示的主體：實際由誰負擔

BEARER_PROMPT = """你是法規標註的獨立稽核員。下面是一個條文單元的原文（含上層條文）與其中一段
義務引文。這段引文本身沒有寫出義務人。只輸出 JSON。

問題：依原文與上層條文，這個義務實際上由誰負擔？
- "bearer"：原文或上層條文裡寫出的義務人，逐字抄（例如「事業」「各級主管機關」）；
  完全推不出就空字串。
- "kind"：
  - "specific"：義務人是原文某個明確的人或機關（可能寫在別項、別款或前句）
  - "general"：沒有限定，任何人都適用（例如一般禁止規定）
  - "unclear"：原文看不出

輸出：{{"bearer": "…", "kind": "specific" 或 "general" 或 "unclear"}}

{context}單元原文（{unit}）：
{text}

義務引文：
{quote}
"""

BEARER_KINDS = ("specific", "general", "unclear")


def ask_bearer(judge: Model, item: Mapping[str, Any], cache: Cache) -> dict[str, Any]:
    context = f"上層條文：\n{item['context']}\n\n" if item["context"] else ""
    prompt = BEARER_PROMPT.format(
        context=context, unit=item["unit"], text=item["text"], quote=item["quote"]
    )
    key = hashlib.sha256(f"{judge.name}\n{prompt}".encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        return hit
    answer: dict[str, Any] | None = None
    for _ in range(RETRIES + 1):
        try:
            answer = object_from(judge.complete(prompt))
        except ModelError:
            answer = None
        if answer is not None:
            break
    kind = str((answer or {}).get("kind", ""))
    bearer = str((answer or {}).get("bearer") or "")
    out = {
        "kind": kind if kind in BEARER_KINDS else "unparsed",
        "bearer": bearer if supported(bearer, item["text"], item["context"]) else "",
        "bearer_claimed": bearer,
    }
    if answer is not None:
        cache.put(key, out)
    return out


def bearer_summary(rows: Sequence[Mapping[str, Any]], judge_names: Sequence[str]) -> dict[str, Any]:
    """兩位判定者對未明示義務的義務人的判斷：兩位都判明確的、都判一般的、其餘。"""
    both_specific = sum(
        all(r["answers"][n]["kind"] == "specific" for n in judge_names) for r in rows
    )
    both_general = sum(all(r["answers"][n]["kind"] == "general" for n in judge_names) for r in rows)
    chain = [r for r in rows if r.get("chain_runs")]
    chain_specific = sum(
        all(r["answers"][n]["kind"] == "specific" for n in judge_names) for r in chain
    )
    chain_general = sum(
        all(r["answers"][n]["kind"] == "general" for n in judge_names) for r in chain
    )
    return {
        "n": len(rows),
        "both_specific": both_specific,
        "both_general": both_general,
        "other": len(rows) - both_specific - both_general,
        "chain_n": len(chain),
        "chain_both_specific": chain_specific,
        "chain_both_general": chain_general,
        "chain_other": len(chain) - chain_specific - chain_general,
    }


# 命令列


def cmd_collect(args: argparse.Namespace) -> None:
    runs: dict[str, list[dict[str, Any]]] = {}
    for spec in args.run:
        version, db = spec.split("=", 1)
        with psycopg.connect(args.dsn.format(db=db)) as conn:
            runs[version] = fetch_confirmed(conn, version)
    items, index = build(runs, args.sample_n, args.seed, args.controls)
    Path(args.items).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(args.index).write_text(
        json.dumps(
            {"seed": args.seed, "sample_n": args.sample_n, "index": index},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    for version, records in runs.items():
        sampled = sum(1 for v in index.values() if v["run"] == version and v.get("sampled"))
        print(f"{version}: 已確認 {len(records)} 筆，抽樣 {sampled} 筆")


def cmd_judge(args: argparse.Namespace) -> None:
    from relevance_judge import Judge  # 同目錄的工具

    config = load_judges(Path(args.models))
    judges: list[Model] = [Judge(m) for m in config.models]  # type: ignore[arg-type]
    if not args.no_distinct_check:
        from envcite_ontology.verification.config import load as load_pipeline
        from envcite_ontology.verification.distinct import assert_distinct

        pipeline = load_pipeline(Path(args.pipeline_models))
        assert_distinct([*judges, *pipeline.everyone])
    items = json.loads(Path(args.items).read_text(encoding="utf-8"))
    cache = Cache(Path(args.cache))
    for attempt in range(1, args.passes + 1):
        rows = judge_all(items, judges, cache, args.workers)
        missing = sum(a["subject"] == "unparsed" for r in rows for a in r["answers"].values())
        print(f"第 {attempt} 輪：{missing} 個答案沒有解析（判定者逾時或沒答），已答的在快取裡")
        if missing == 0:
            break
    Path(args.out).write_text(
        json.dumps(
            {
                "judges": [j.name for j in judges],
                "families": {j.name: j.family for j in judges},
                "system_families": sorted(config.system_families),
                "prompt_sha256": prompt_sha(),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(rows)} 筆，判定者 {[j.name for j in judges]}")


def cmd_bearer(args: argparse.Namespace) -> None:
    from relevance_judge import Judge

    config = load_judges(Path(args.models))
    judges: list[Model] = [Judge(m) for m in config.models]  # type: ignore[arg-type]
    cache = Cache(Path(args.cache))
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for run_name, run in json.loads(Path(args.unstated).read_text(encoding="utf-8")).items():
        for it in run["items"]:
            entry = seen.setdefault((it["unit"], it["quote"]), {**it, "runs": [], "chain_runs": []})
            entry["runs"].append(run_name)
            if it["penalties"]:
                entry["chain_runs"].append(run_name)
    rows = []
    for (unit, quote), it in sorted(seen.items()):
        item = {"unit": unit, "quote": quote, "text": it["text"], "context": it["context"]}
        answers = {j.name: ask_bearer(j, item, cache) for j in judges}
        rows.append(
            {
                **item,
                "answers": answers,
                "runs": it["runs"],
                "chain_runs": it["chain_runs"],
                "penalties": it["penalties"],
            }
        )
    names = [j.name for j in judges]
    Path(args.out).write_text(
        json.dumps(
            {"judges": names, "summary": bearer_summary(rows, names), "rows": rows},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(rows)} 項（單元、引文不重複）")


def cmd_report(args: argparse.Namespace) -> None:
    index_doc = json.loads(Path(args.index).read_text(encoding="utf-8"))
    judgements = json.loads(Path(args.judgements).read_text(encoding="utf-8"))
    result = report_dict(index_doc["index"], judgements, args.base, args.treated)
    result["design"] = {
        "seed": index_doc["seed"],
        "sample_n_per_run": index_doc["sample_n"],
        "families": judgements["families"],
        "system_families": judgements["system_families"],
    }
    # 判定列只留可對回的最小內容；單元原文在法規資料庫，不複製進報告
    result["rows"] = [
        {
            "run": index_doc["index"][r["key"]]["run"],
            "id": index_doc["index"][r["key"]].get("id"),
            "unit": index_doc["index"][r["key"]].get("unit"),
            "round": index_doc["index"][r["key"]].get("round"),
            "sampled": index_doc["index"][r["key"]].get("sampled"),
            "control": index_doc["index"][r["key"]].get("control"),
            "answers": r["answers"],
        }
        for r in judgements["rows"]
    ]
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    col = sub.add_parser("collect")
    col.add_argument("--dsn", required=True, help="含 {db} 的連線字串")
    col.add_argument("--run", action="append", required=True, help="版本=庫名，可重複")
    col.add_argument("--sample-n", type=int, default=30)
    col.add_argument("--seed", type=int, default=20261109)
    col.add_argument("--controls", action="store_true", help="另加改壞的對照項目")
    col.add_argument("--items", required=True)
    col.add_argument("--index", required=True)
    jud = sub.add_parser("judge")
    jud.add_argument("items")
    jud.add_argument("--models", default="eval/duty-fix-effect-judges.toml")
    jud.add_argument("--pipeline-models", default="models.toml")
    jud.add_argument("--no-distinct-check", action="store_true")
    jud.add_argument("--cache", required=True)
    jud.add_argument("--workers", type=int, default=8)
    jud.add_argument("--passes", type=int, default=4, help="沒答的再問幾輪")
    jud.add_argument("--out", required=True)
    bea = sub.add_parser("bearer")
    bea.add_argument("unstated", help="duty_cost.py unstated 的輸出")
    bea.add_argument("--models", default="eval/duty-fix-effect-judges.toml")
    bea.add_argument("--cache", required=True)
    bea.add_argument("--out", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("index")
    rep.add_argument("judgements")
    rep.add_argument("--base", default="pre")
    rep.add_argument("--treated", default="post")
    rep.add_argument("--out", required=True)
    args = parser.parse_args()
    {"collect": cmd_collect, "judge": cmd_judge, "bearer": cmd_bearer, "report": cmd_report}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()
