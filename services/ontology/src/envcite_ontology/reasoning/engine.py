"""通用推論程式：某日有效的陳述加上案件事實，得出候選結論、缺什麼、需判斷什麼。

推論程式（`rules.lp`）不含法條，陳述是資料。結論皆為**候選**（違反、合規、附條件、被排除、
不適用、無法判定），罰則只列不裁量。行政罰法總則排除的是處罰：事由成立則不列，有責性未知則
附條件，義務陳述的結論不變。罰鍰同列兩種區間（各則自己的額度；確定且上下限皆有者另合併為
一行為的區間），不選其一。優先關係僅在明文標註時消解，否則列為未決。

`order`（改善命令等預防性處分）與 `disgorgement`（不法利得的追繳）不是行政罰：總則的排除、減輕、
時效與一行為皆不適用。罰則 `discretionary` 是效果裁量（條文寫「得」），與要件的需判斷分屬兩層。
以另一個效果為前提的罰則（`annotation.sanction_trigger`），前提列出才列出，`triggered_by` 帶前提。

案件事實之外，呼叫端可給事實的認定方式（`measurements`）與場所持有的許可所載的量值（`permits`）：
授權限值標有認定方式時，方式不符或沒給則要件未知；委由個別處分的要件，以許可的量值為門檻值。
罰則的主體槽位標有責任基礎時，呼叫端說明行為人所屬的基礎（`actor_bases`）要在其中：不在則主體
要件不成立、罰則不列（`subject_mismatch`），沒說明則缺事實（概念 `liability_basis`）、罰則附條件。

違憲失效的條文版本自失效日起不是依據：讀它的陳述不進推論，列在 `invalidated`。命令的陳述帶
授權鏈（`delegation`）；鏈到不了法律時，命令所定的罰則與處罰命令義務的罰則附條件。

第 18 條：得減輕的罰鍰另給減輕後的範圍（`General.reduced`），所得利益超過最高額者標得加重
（`General.raised_caps`）；審酌與選定額度不做。第 26 條：一行為同時觸犯刑事法律則罰鍰不列
（`criminal_priority`），刑事程序以不起訴等確定後照列並給扣抵額（`General.deductions`）。
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from importlib import resources
from typing import Any

import clingo

from envcite_ontology.db import Conn
from envcite_ontology.reasoning import facts

_RULES = resources.files(__package__).joinpath("rules.lp").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Penalty:
    """候選罰則；`conditional` 為罰則要件未知，或被處罰的陳述僅附條件違反。"""

    statement_id: int
    punishes_id: int
    effect: str
    min_amount: int | None
    max_amount: int | None
    conditional: bool
    repeating: bool
    discretionary: bool
    #: 作為前提的罰則與觸發種類（`order_lapsed`、`sanction_imposed`）
    triggered_by: tuple[tuple[int, str], ...] = ()
    #: 罰則主體槽位標註的責任基礎（`act`、`state`、`representative`）；沒有標註為空
    subject_bases: tuple[str, ...] = ()


@dataclass(frozen=True)
class General:
    """行政罰法總則對罰則的標記；排除事由成立者不在 `penalties`。"""

    unpunished: tuple[tuple[int, str], ...]
    mitigation: tuple[tuple[int, str], ...]
    presumed: tuple[tuple[int, str], ...]
    time_barred: tuple[int, ...]
    #: （罰則，減輕事由，分母 2 或 3，下限，上限）：第 18 條第 3 項；缺的額度為 None
    reduced: tuple[tuple[int, str, int, int | None, int | None], ...] = ()
    #: （罰則，所得利益）：第 18 條第 2 項，得在所得利益範圍內加重
    raised_caps: tuple[tuple[int, Decimal], ...] = ()
    #: （罰則，扣抵額）：第 26 條第 3、4 項
    deductions: tuple[tuple[int, Decimal], ...] = ()


@dataclass(frozen=True)
class Trace:
    """要件原子實際比較的事實值與門檻值（千分位）。"""

    condition_id: int
    statement_id: int
    concept: str
    fact: int
    threshold: int
    role: str


@dataclass(frozen=True)
class Result:
    """一次推論的結果：各陳述的候選結論與罰則、缺的事實、需判斷處，及推導的依據。"""

    verdicts: Mapping[int, str]
    penalties: tuple[Penalty, ...]
    one_act: tuple[int, int] | None
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
    general: General
    #: （要件編號，定義編號）：概念值要件所依的用語定義
    definitions_used: tuple[tuple[int, int], ...] = ()
    #: （陳述，許可種類，概念）：委由個別處分、但呼叫端沒提供許可量值的要件
    missing_permits: tuple[tuple[int, str, str], ...] = ()
    #: 其餘要件成立或未知、但主體的責任基礎與行為人所屬不符而不列出的罰則
    subject_mismatch: tuple[int, ...] = ()
    #: 該日因違憲失效而不參與推論的陳述
    invalidated: tuple[facts.Invalidated, ...] = ()
    #: 命令陳述 → 其命令的授權鏈
    delegation: Mapping[int, facts.Chain] = field(default_factory=dict)


def _py(symbol: clingo.Symbol) -> Any:
    if symbol.type == clingo.SymbolType.Number:
        return symbol.number
    if symbol.type == clingo.SymbolType.String:
        return symbol.string
    return symbol.name


def evaluate(
    conn: Conn,
    as_of: date,
    case: facts.Case,
    language: str = "zho",
    *,
    include_unsettled: bool = False,
    decided_on: date | None = None,
    measurements: facts.Measurements | None = None,
    permits: facts.Permits | None = None,
    actor_bases: Collection[str] | None = None,
) -> Result:
    """對 `as_of` 當日有效的陳述與案件事實推論。

    `decided_on` 只用於裁處權時效，預設同 `as_of`；跨法律變更見 `transition.evaluate_across`。
    `include_unsettled` 僅供驗證閘門在接受前評估陳述，推論呼叫端不可用。
    """
    statements = facts.statement_facts(conn, as_of, language, include_unsettled=include_unsettled)
    given = facts.amounts(case)
    program = "\n".join(
        [
            statements.program,
            facts.case_facts(case, measurements, permits, actor_bases),
            facts.limitation(decided_on or as_of, case),
            facts.amount_facts(given, statements.fine_caps),
        ]
    )
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
    if len(models) != 1:  # 規則確定，非單一答案集即規則或事實有誤
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
    flags = {
        int(row[0]): (bool(row[1]), bool(row[2]))  # type: ignore[call-overload]
        for row in conn.execute(
            "SELECT statement_id, per_occasion, discretionary FROM annotation.sanction"
            " WHERE statement_id = ANY(%s)",
            ([c[0] for c in candidates],),
        )
    }
    triggers: dict[int, list[tuple[int, str]]] = {}
    for p, q, kind in get("trigger"):
        triggers.setdefault(int(p), []).append((int(q), str(kind)))
    bases: dict[int, list[str]] = {}
    for p, basis in get("subject_basis"):
        bases.setdefault(int(p), []).append(str(basis))
    penalties = [
        Penalty(
            *c,
            repeating=flags[c[0]][0],
            discretionary=flags[c[0]][1],
            triggered_by=tuple(triggers.get(c[0], ())),
            subject_bases=tuple(sorted(set(bases.get(c[0], ())))),
        )
        for c in candidates
    ]
    shown = get("one_act")
    if len(shown) > 1:
        raise RuntimeError(f"expected one fine interval, got {len(shown)}")
    one_act = None if not shown else (int(shown[0][0]), int(shown[0][1]))
    raised = (
        ()
        if given.benefit is None
        else tuple((int(p), given.benefit) for (p,) in get("aggravable"))
    )
    deductions = (
        ()
        if given.deduction is None
        else tuple((int(p), given.deduction) for (p,) in get("deduct"))
    )
    return Result(
        verdicts={s: v for s, v in get("verdict")},
        penalties=tuple(sorted(penalties, key=lambda x: (x.statement_id, x.punishes_id))),
        one_act=one_act,
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
        general=General(
            unpunished=tuple((int(p), str(g)) for p, g in get("barred")),
            mitigation=tuple((int(p), str(g)) for p, g in get("mitigation")),
            presumed=tuple((int(p), str(g)) for p, g in get("presumed")),
            time_barred=tuple(int(p) for (p,) in get("time_barred")),
            reduced=tuple(
                (int(p), str(g), int(k), amount(lo), amount(hi))
                for p, g, k, lo, hi in get("reduced")
            ),
            raised_caps=raised,
            deductions=deductions,
        ),
        definitions_used=tuple((int(c), int(d)) for c, d in get("used_def")),
        missing_permits=tuple((int(s), str(k), str(m)) for s, k, m in get("missing_permit")),
        subject_mismatch=tuple(int(p) for (p,) in get("subject_mismatch")),
        invalidated=statements.invalidated,
        delegation=statements.chains,
    )
