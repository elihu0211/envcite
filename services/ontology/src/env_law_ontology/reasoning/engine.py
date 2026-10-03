"""通用推論程式：某日有效的陳述加上案件事實，得出候選結論、缺什麼、需判斷什麼。

設計見 env-law-docs 02-05 §5。

推論程式（`rules.lp`）不含任何法條，陳述是資料。結論都是**候選**：違反、合規、附條件、被排除、
不適用、無法判定；罰則只列候選，不裁量；優先關係只在有明文標註時消解，否則列為未決。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Any

import clingo
import psycopg

from env_law_ontology.reasoning import facts

Conn = psycopg.Connection[tuple[object, ...]]

_RULES = resources.files(__package__).joinpath("rules.lp").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Penalty:
    """候選罰則。`conditional`：罰則自己的要件未知（或被處罰的陳述只是附條件違反）。"""

    statement_id: int
    punishes_id: int
    effect: str
    min_amount: int | None
    max_amount: int | None
    conditional: bool
    repeating: bool


@dataclass(frozen=True)
class Trace:
    """某個要件原子的取值：用了哪個事實值、哪個門檻值（千分位）。"""

    condition_id: int
    statement_id: int
    concept: str
    fact: int
    threshold: int
    role: str


@dataclass(frozen=True)
class Result:
    verdicts: Mapping[int, str]
    penalties: tuple[Penalty, ...]
    missing: tuple[tuple[int, str], ...]
    needs_judgement: tuple[tuple[int, str], ...]
    judgement_used: tuple[tuple[int, str], ...]
    problems: tuple[tuple[str, int | str], ...]
    unresolved_conflicts: tuple[tuple[int, int], ...]
    defeated: tuple[tuple[int, int, str], ...]
    unresolved_penalty_overlaps: tuple[tuple[int, int], ...]
    penalty_defeated: tuple[tuple[int, int, str], ...]
    trace: tuple[Trace, ...]
    limit_sources: Mapping[int, int]


def _py(symbol: clingo.Symbol) -> Any:
    if symbol.type == clingo.SymbolType.Number:
        return symbol.number
    if symbol.type == clingo.SymbolType.String:
        return symbol.string
    return symbol.name


def evaluate(
    conn: Conn,
    as_of: date,
    case: Mapping[str, float | Sequence[float]],
    language: str = "zho",
    *,
    include_unsettled: bool = False,
) -> Result:
    """對 `as_of` 當日有效的陳述與給定的案件事實推論。

    `include_unsettled` 只給驗證閘門用（在陳述被接受之前評估它）；推論的呼叫端不該用。
    """
    statements = facts.statement_facts(conn, as_of, language, include_unsettled=include_unsettled)
    program = "\n".join([statements, facts.case_facts(case)])
    ctl = clingo.Control(["--warn=none"])
    ctl.add("base", [], _RULES)
    ctl.add("base", [], program)
    ctl.ground([("base", [])])
    models: list[list[tuple[str, tuple[Any, ...]]]] = []
    ctl.solve(
        on_model=lambda m: models.append(
            [(s.name, tuple(_py(a) for a in s.arguments)) for s in m.symbols(shown=True)]
        )
    )
    if len(models) != 1:  # 規則是確定的：不是一個答案集就是規則或事實有誤
        raise RuntimeError(f"expected exactly one answer set, got {len(models)}")
    atoms: dict[str, list[tuple[Any, ...]]] = {}
    for name, args in models[0]:
        atoms.setdefault(name, []).append(args)

    def get(name: str) -> list[tuple[Any, ...]]:
        return sorted(atoms.get(name, []), key=repr)

    def amount(value: Any) -> int | None:
        return None if value == "none" else int(value)

    candidates = [
        (p, s, e, amount(lo), amount(hi), conditional)
        for name, conditional in (("candidate_penalty", False), ("candidate_penalty_cond", True))
        for p, s, e, lo, hi in get(name)
    ]
    repeating = {
        int(row[0])  # type: ignore[call-overload]
        for row in conn.execute(
            "SELECT statement_id FROM annotation.sanction"
            " WHERE per_occasion AND statement_id = ANY(%s)",
            ([c[0] for c in candidates],),
        )
    }
    penalties = [Penalty(*c, repeating=c[0] in repeating) for c in candidates]
    return Result(
        verdicts={s: v for s, v in get("verdict")},
        penalties=tuple(sorted(penalties, key=lambda x: (x.statement_id, x.punishes_id))),
        missing=tuple((s, m) for s, m in get("missing")),
        needs_judgement=tuple((s, m) for s, m in get("needs_judgement")),
        judgement_used=tuple((s, m) for s, m in get("judgement_used")),
        problems=tuple((k, v) for k, v in get("problem")),
        unresolved_conflicts=tuple((a, b) for a, b in get("unresolved_conflict")),
        defeated=tuple((a, b, c) for a, b, c in get("defeated")),
        unresolved_penalty_overlaps=tuple((a, b) for a, b in get("unresolved_penalty_overlap")),
        penalty_defeated=tuple((a, b, c) for a, b, c in get("penalty_defeated")),
        trace=tuple(Trace(*t) for t in get("trace")),
        limit_sources={c: s for c, s in get("trace_src")},
    )
