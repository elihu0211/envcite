"""量測抽取者宣告概念前先看到既有概念，是否少產生意思重複的新概念。

全程沒有人工標籤：
- 取已有標註的法規裡還沒有任何陳述的條文單元（它們的概念還不在方案裡，既有概念是真正的先備知識，
  沒有洩題），各用兩種提示讓同一個抽取者抽取（不寫入資料庫）：不給既有概念，與給 `known_concepts`
  挑的相關既有概念；
- 給的清單同名只留最早的一個，與合併同名概念之後的方案一致；
- 抽取者宣告的每個概念分三類：名稱與既有概念相同（沿用）；名稱不在既有概念裡、但另一個家族的
  模型判斷與其中某個意思相同（意思重複）；其餘是真正的新概念。

要看的是「意思重複」的比例有沒有降，同一個判斷者、同一批單元，兩種提示各量一次。

用法（從 services/canon 執行）::

    ENVCITE_CANON_DSN=... uv run python tools/concept_reuse_eval.py --out <目錄>
"""

import argparse
import json
import random
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

import psycopg

from canon.db import Conn, fetch
from canon.settings import DatabaseSettings, load
from canon.verification import prompts
from canon.verification.checks.shape import concepts_of
from canon.verification.config import load_declared, load_embedder
from canon.verification.context import KnownConcept, ancestors_text, known_concepts
from canon.verification.extract import _unit_text
from canon.verification.jsonutil import object_from
from canon.verification.models import ChatCompletionsModel
from fact_extraction_eval import Cache, cosine

#: 抽取者用標註管線裡的抽取模型；判斷者必須是另一個家族。
EXTRACTOR_ID = "mistral-small-4"
JUDGE_ID = "ornith-35b"
SCHEME = "env"
#: 判斷「意思是否重複」時給判斷者看的最相近的清單概念數。
NEIGHBOURS = 8


_UNITS = """
SELECT r.local_id, p.path
  FROM corpus.provision AS p
  JOIN corpus.legal_resource AS r ON r.id = p.resource_id AND r.id_scheme = 'moj_pcode'
  JOIN corpus.provision_version AS v ON v.provision_id = p.id AND v.superseded_at IS NULL
 WHERE r.id IN (SELECT DISTINCT q.resource_id FROM annotation.statement AS s
                  JOIN corpus.provision AS q ON q.id = s.provision_id)
   AND NOT EXISTS (SELECT 1 FROM annotation.statement AS s WHERE s.provision_id = p.id)
   AND v.content ~ '(應|不得|禁止|處)' AND char_length(v.content) BETWEEN 30 AND 300
 ORDER BY r.local_id, p.path
"""


class _Concept(NamedTuple):
    id: int
    code: str
    name: str


_CONCEPTS = """
SELECT k.id, k.code, k.name FROM vocab.concept AS k
  JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id WHERE s.code = %s ORDER BY k.id
"""


class Unit(NamedTuple):
    law: str
    path: str


def units(conn: Conn, seed: int, count: int) -> list[Unit]:
    """已有標註的法規裡、還沒有陳述的規範性條文單元，隨機取 `count` 個。"""
    out = fetch(conn, _UNITS, (), Unit)
    random.Random(seed).shuffle(out)
    return out[:count]


def offered(conn: Conn, unit: Unit) -> list[KnownConcept]:
    """給抽取者的既有概念：相關的、同名只留最早的一個。"""
    _, text = _unit_text(conn, unit.law, unit.path)
    seen: set[str] = set()
    out = []
    for k in known_concepts(conn, SCHEME, text, limit=200):
        if k.name not in seen:
            seen.add(k.name)
            out.append(k)
    return out[:40]


def extraction_prompt(conn: Conn, unit: Unit, known: Sequence[KnownConcept]) -> str:
    """與管線相同的抽取提示；沒給的部分（本法已定義的用語等）兩種條件都一樣空著。"""
    provision_id, text = _unit_text(conn, unit.law, unit.path)
    return prompts.extract(
        law=unit.law,
        path=unit.path,
        text=text,
        context=ancestors_text(conn, provision_id),
        defined=None,
        punished_by=(),
        review=(),
        known=known,
    )


def judge_prompt(label: str, near: Sequence[KnownConcept]) -> str:
    """新宣告的概念與清單裡最相近的幾個：意思是否相同。"""
    lines = "\n".join(f"- {k.code}｜{k.name}" for k in near)
    return (
        "下面是法規標註裡新宣告的一個概念名稱，與既有概念的清單。判斷新概念的意思是否與清單裡"
        "某一個相同（同一個法律上的事實或要件，只是換了說法）；只是相關、範圍較大或較小、"
        "或是它的前提或結果，都不算相同。\n"
        f"新概念：{label}\n既有概念：\n{lines}\n"
        '只輸出 JSON：{"same_as":"代碼或 none"}'
    )


def declared(raw: str) -> list[tuple[str, str]]:
    """模型回答裡宣告的概念（代碼、名稱）。"""
    answer = object_from(raw)
    if answer is None:
        return []
    return [(str(c["code"]), str(c["label"])) for c in concepts_of(answer)]


def main() -> None:
    """跑兩種提示，各自分類宣告的概念，寫出逐單元結果與彙總。"""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", type=Path, default=Path("models.toml"))
    ap.add_argument("--seed", type=int, default=20261118)
    ap.add_argument("--units", type=int, default=40)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cache", type=Path)
    args = ap.parse_args()

    pick: dict[str, ChatCompletionsModel] = {
        m.model_id: m
        for m in load_declared(args.models).values()
        if isinstance(m, ChatCompletionsModel)
    }
    extractor, judge = pick[EXTRACTOR_ID], pick[JUDGE_ID]
    if extractor.family == judge.family:
        raise SystemExit("抽取者與判斷者必須屬不同家族")
    embedder = load_embedder(args.models, timeout=60.0)
    if embedder is None:
        raise SystemExit("models.toml 沒有 [embedding]")
    args.out.mkdir(parents=True, exist_ok=True)
    cache = Cache(args.cache or args.out / "model-cache.json")

    with psycopg.connect(load(DatabaseSettings).dsn) as conn:
        conn.read_only = True
        chosen = units(conn, args.seed, args.units)
        all_concepts = fetch(conn, _CONCEPTS, (SCHEME,), _Concept)
        plans = [(u, offered(conn, u)) for u in chosen]

        def condition(plan: tuple[Unit, list[KnownConcept]], shown: bool) -> Any:
            u, known = plan
            raw = cache.get(extractor, extraction_prompt(conn, u, known if shown else ()))
            return u, declared(raw)

        with ThreadPoolExecutor(args.workers) as pool:
            results = {
                label: list(pool.map(lambda p, s=shown: condition(p, s), plans))
                for label, shown in (("without", False), ("with", True))
            }
        cache.save()

    # 分類以每個單元「看得到的既有概念」為準：全部概念拿掉只有它自己用到的
    summary: dict[str, Any] = {"units": len(chosen)}
    rows: dict[str, list[dict[str, Any]]] = {}
    for label, per_unit in results.items():
        counts: Counter[str] = Counter()
        rows[label] = []
        for u, decl in per_unit:
            visible = [KnownConcept(c.code, c.name) for c in all_concepts]
            names = {k.name for k in visible}
            vectors = dict(
                zip(
                    (k.code for k in visible),
                    cache.embed(embedder, [k.name for k in visible]),
                    strict=True,
                )
            )
            unit_row: dict[str, Any] = {"law": u.law, "path": u.path, "declared": []}
            for code, name in decl:
                counts["declared"] += 1
                if name in names:
                    counts["reused"] += 1
                    kind = "reused"
                else:
                    query = cache.embed(embedder, [name])[0]
                    near = sorted(visible, key=lambda k: -cosine(vectors[k.code], query))
                    near = near[:NEIGHBOURS]
                    reply = cache.get(judge, judge_prompt(name, near))
                    answer = object_from(reply) or {}
                    twin = str(answer.get("same_as", "none"))
                    if any(k.code == twin for k in near):
                        counts["duplicate"] += 1
                        kind = f"duplicate of {twin}"
                    else:
                        counts["new"] += 1
                        kind = "new"
                unit_row["declared"].append({"code": code, "name": name, "kind": kind})
            rows[label].append(unit_row)
        summary[label] = dict(counts)
    cache.save()
    (args.out / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
