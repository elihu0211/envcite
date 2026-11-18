"""量化方案裡「意思相近、名稱不同」的既有概念有多少，以及它們牽動多少陳述。

同名的重複由唯一索引擋下；名稱不同但意思相同的概念（「排放廢（污）水」與「排放行為」）各被不同的
陳述用到，同一個事實就湊不齊同一批要件。這裡只量、不改資料庫：

- 候選配對：每個被陳述用到的概念，取名稱向量最相近的幾個；
- 判斷：兩位不同家族的模型各自判斷兩個概念是不是同一個法律上的事實或要件（只是換了說法），
  兩位都說是才算意思重複；全程沒有人工標籤；
- 用法相容才可能合併：同為旗標，或同單位的數值。

用法（從 services/canon 執行）::

    ENVCITE_CANON_DSN=... uv run python tools/concept_twins.py --out <目錄>
"""

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

import psycopg

from canon.db import Conn, fetch
from canon.settings import DatabaseSettings, load
from canon.verification.config import load_declared, load_embedder
from canon.verification.jsonutil import object_from
from canon.verification.models import ChatCompletionsModel
from fact_extraction_eval import Cache, cosine

#: 判斷者：兩位、不同家族。
JUDGE_IDS = ("ornith-35b", "nemotron-3.5-super-vl")
SCHEME = "env"
#: 每個概念取名稱最相近的幾個當候選配對。
NEIGHBOURS = 5


class _Use(NamedTuple):
    concept_id: int
    code: str
    name: str
    statement_id: int
    comparator: str
    threshold: float | None
    unit: str | None


_USES = """
SELECT k.id, k.code, k.name, c.statement_id, c.comparator::text, c.threshold::float8, c.measure_unit
  FROM annotation.statement_condition AS c
  JOIN annotation.assertion AS a ON a.id = c.statement_id AND a.confirm_state <> 'rejected'
  JOIN vocab.concept AS k ON k.id = c.concept_id
  JOIN vocab.concept_scheme AS s ON s.id = k.scheme_id AND s.code = %s
"""


class Concept(NamedTuple):
    id: int
    code: str
    name: str
    shape: str  # flag、number:<單位>，或 mixed（用法不只一種，不合併）
    statements: frozenset[int]


def concepts(conn: Conn) -> list[Concept]:
    """被陳述的要件用到的概念，連同用法與用到它的陳述。"""
    names: dict[int, tuple[str, str]] = {}
    units: dict[int, set[str]] = defaultdict(set)
    used: dict[int, set[int]] = defaultdict(set)
    for u in fetch(conn, _USES, (SCHEME,), _Use):
        names[u.concept_id] = (u.code, u.name)
        used[u.concept_id].add(u.statement_id)
        flag = u.comparator == "eq" and u.threshold == 1.0 and u.unit is None
        if u.threshold is not None and not flag:
            units[u.concept_id].add(u.unit or "")
    out = []
    for cid, (code, name) in sorted(names.items()):
        if not units[cid]:
            shape = "flag"
        elif len(units[cid]) == 1:
            shape = f"number:{next(iter(units[cid]))}"
        else:
            shape = "mixed"
        out.append(Concept(cid, code, name, shape, frozenset(used[cid])))
    return out


def judge_prompt(a: str, b: str) -> str:
    """兩個概念名稱是不是同一個法律上的事實或要件。"""
    return (
        "下面是法規標註裡的兩個概念名稱。判斷它們是不是同一個法律上的事實或要件，只是換了說法"
        "（在同一個案件裡，一個成立另一個必然成立、一個不成立另一個必然不成立）。"
        "只是相關、範圍較大或較小、或是前提與結果，都不算。\n"
        f'概念甲：{a}\n概念乙：{b}\n只輸出 JSON：{{"same":true}} 或 {{"same":false}}'
    )


def same(raw: str) -> bool:
    """判斷者的回答是不是「同一個」。"""
    answer = object_from(raw) or {}
    return answer.get("same") is True


def main() -> None:
    """找出意思重複的概念對，寫出清單與彙總。"""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--models", type=Path, default=Path("models.toml"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--cache", type=Path)
    args = ap.parse_args()

    pick: dict[str, ChatCompletionsModel] = {
        m.model_id: m
        for m in load_declared(args.models).values()
        if isinstance(m, ChatCompletionsModel)
    }
    judges = [pick[j] for j in JUDGE_IDS]
    if len({j.family for j in judges}) != len(judges):
        raise SystemExit("兩位判斷者必須屬不同家族")
    embedder = load_embedder(args.models, timeout=60.0)
    if embedder is None:
        raise SystemExit("models.toml 沒有 [embedding]")
    args.out.mkdir(parents=True, exist_ok=True)
    cache = Cache(args.cache or args.out / "model-cache.json")

    with psycopg.connect(load(DatabaseSettings).dsn) as conn:
        conn.read_only = True
        used = concepts(conn)
    vectors = dict(
        zip((c.id for c in used), cache.embed(embedder, [c.name for c in used]), strict=True)
    )
    pairs: set[tuple[int, int]] = set()
    for c in used:
        near = sorted(
            (o for o in used if o.id != c.id), key=lambda o: -cosine(vectors[c.id], vectors[o.id])
        )
        for o in near[:NEIGHBOURS]:
            pairs.add((min(c.id, o.id), max(c.id, o.id)))
    by_id = {c.id: c for c in used}

    def verdicts(pair: tuple[int, int]) -> list[bool]:
        a, b = by_id[pair[0]], by_id[pair[1]]
        prompt = judge_prompt(a.name, b.name)
        return [same(cache.get(j, prompt)) for j in judges]

    ordered = sorted(pairs)
    with ThreadPoolExecutor(args.workers) as pool:
        votes = list(pool.map(verdicts, ordered))
    cache.save()

    twins: list[dict[str, Any]] = []
    by_any = 0
    for pair, vote in zip(ordered, votes, strict=True):
        by_any += int(any(vote))
        if not all(vote):
            continue
        a, b = by_id[pair[0]], by_id[pair[1]]
        twins.append(
            {
                "a": {"code": a.code, "name": a.name, "statements": len(a.statements)},
                "b": {"code": b.code, "name": b.name, "statements": len(b.statements)},
                "mergeable": a.shape == b.shape != "mixed",
                "shape": [a.shape, b.shape],
                "together_in_a_statement": len(a.statements & b.statements),
            }
        )
    involved = {t["a"]["code"] for t in twins} | {t["b"]["code"] for t in twins}
    statements: set[int] = set()
    for t in twins:
        for side in ("a", "b"):
            code = t[side]["code"]
            statements |= next(c.statements for c in used if c.code == code)
    summary = {
        "used_concepts": len(used),
        "candidate_pairs": len(ordered),
        "judged_same_by_any": by_any,
        "judged_same_by_both": len(twins),
        "mergeable": sum(1 for t in twins if t["mergeable"]),
        "concepts_involved": len(involved),
        "statements_touched": len(statements),
        "judges": [j.name for j in judges],
    }
    (args.out / "twins.json").write_text(json.dumps(twins, ensure_ascii=False, indent=1))
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for t in twins[:40]:
        print(
            f"{t['a']['name']}（{t['a']['statements']}）≈ {t['b']['name']}（{t['b']['statements']}）"
        )


if __name__ == "__main__":
    main()
