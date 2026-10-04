"""驗證閘門：每個閘門檢查標註的一個性質，回報通過、失敗、無法判定，或不適用。

閘門檢查的是「標註與原文、與推論程式是否一致」，不是「法律上對不對」。這裡的三個都是確定性的
工具，不呼叫語言模型：

- `numbers`：罰則的額度與帶單位的門檻值，必須與原文的數字一致（中文數字與阿拉伯數字都讀）。
- `references`：罰則所處罰的陳述，必須是原文引用的那一條（項）。
- `executable`：規範性陳述的結論隨義務方向翻轉；罰則只在所處罰的陳述已違反、
  且自己的適用範圍成立時成為候選。

它們互相獨立，也獨立於抽取者；但沒有一個能證明要件的語意、陳述的種類或義務方向忠於原文——那需要
往返驗證、獨立測試或跨家族評審（設計見 02-05 §5.9），它們要呼叫模型，不在這裡。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

import psycopg

from env_law_ontology.reasoning import Result, evaluate
from env_law_ontology.verification.context import ancestors_text

Conn = psycopg.Connection[tuple[object, ...]]
Verdict = Literal["pass", "fail", "inconclusive"]


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    evidence: dict[str, Any] = field(default_factory=dict)


class Gate(Protocol):
    """一個閘門。`check` 回 None 表示對這個陳述不適用。`verifier` 記入紀錄，且不得等於抽取者。"""

    @property
    def method(self) -> str: ...

    @property
    def verifier(self) -> str: ...

    def check(self, conn: Conn, statement_id: int) -> GateResult | None: ...


@dataclass(frozen=True)
class _Unit:
    statement_id: int
    provision_id: int
    path: str
    resource_id: int
    text: str
    span: tuple[int, int] | None
    kind: str
    deontic: str | None
    context: str = ""  # 上層條文：只供讀，錨點與引文都在 text 上

    @property
    def quoted(self) -> str:
        return self.text if self.span is None else self.text[self.span[0] : self.span[1]]

    @property
    def with_context(self) -> str:
        """罰則的額度可能寫在上層的項裡，款自己的引文裡沒有。"""
        return f"{self.context}\n{self.quoted}" if self.context else self.quoted


def _unit(conn: Conn, statement_id: int) -> _Unit:
    row = conn.execute(
        "SELECT s.provision_id, p.path, p.resource_id, v.content, lower(s.span), upper(s.span),"
        "       s.statement_kind::text, s.norm_modality::text"
        "  FROM annotation.statement AS s"
        "  JOIN corpus.provision AS p ON p.id = s.provision_id"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE s.id = %s LIMIT 1",
        (statement_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"statement {statement_id} has no current text to check against")
    pid, path, rid, text, lo, hi, kind, deontic = row
    span = None if lo is None else (cast(int, lo), cast(int, hi))
    return _Unit(
        statement_id,
        cast(int, pid),
        cast(str, path),
        cast(int, rid),
        cast(str, text),
        span,
        cast(str, kind),
        cast("str | None", deontic),
        ancestors_text(conn, cast(int, pid)),
    )


# ---- 中文數字 ----------------------------------------------------------------------------------

_DIGIT = {c: i for i, c in enumerate("〇一二三四五六七八九")} | {"零": 0}
_UNIT = {"十": 10, "百": 100, "千": 1000}
NUMBER = "0-9,〇零一二三四五六七八九十百千萬億"


def cn_number(s: str) -> int:
    """「六萬」→ 60000、「二千萬」→ 20000000、「一百」→ 100；讀不出拋 ValueError。"""
    s = s.replace(",", "").replace(" ", "")
    if not s:
        raise ValueError(s)
    if s.isdigit():
        return int(s)
    for mark, scale in (("億", 10**8), ("萬", 10**4)):
        if mark in s:
            head, _, tail = s.partition(mark)
            return (cn_number(head) if head else 1) * scale + (cn_number(tail) if tail else 0)
    total = cur = 0
    for ch in s:
        if ch in _DIGIT:
            cur = _DIGIT[ch]
        elif ch in _UNIT:
            total += (cur or 1) * _UNIT[ch]
            cur = 0
        else:
            raise ValueError(s)
    return total + cur


_MONEY = re.compile(rf"([{NUMBER}]+)元(以上|以下)?")


def amounts(text: str) -> list[tuple[int, str | None]]:
    """原文中的金額及其限定詞（以上、以下），依出現順序。讀不出的略過。"""
    out: list[tuple[int, str | None]] = []
    for m in _MONEY.finditer(text):
        try:
            out.append((cn_number(m.group(1)), m.group(2)))
        except ValueError:
            continue
    return out


def _ranges(found: Sequence[tuple[int, str | None]]) -> list[tuple[int | None, int | None]]:
    """把「以上」「以下」配成額度的組：（下限, 上限）；只有一邊的，另一邊是 None。"""
    out: list[tuple[int | None, int | None]] = []
    low: int | None = None
    for value, qualifier in found:
        if qualifier == "以上":
            if low is not None:
                out.append((low, None))
            low = value
        elif qualifier == "以下":
            out.append((low, value))
            low = None
    if low is not None:
        out.append((low, None))
    return out


def numbers_in(text: str) -> set[int]:
    """原文中所有讀得出的整數（中文或阿拉伯數字）。"""
    found: set[int] = set()
    for m in re.finditer(rf"[{NUMBER}]+", text):
        try:
            found.add(cn_number(m.group(0)))
        except ValueError:
            continue
    return found


# ---- 閘門一：數字 ------------------------------------------------------------------------------


@dataclass(frozen=True)
class NumbersGate:
    """罰則的額度與帶單位的門檻值，與原文的數字一致。"""

    method: str = "numbers"
    verifier: str = "gate:numbers@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = _unit(conn, statement_id)
        checks: list[dict[str, Any]] = []
        sanction = conn.execute(
            "SELECT amount_text, min_amount, max_amount FROM annotation.sanction"
            " WHERE statement_id = %s",
            (statement_id,),
        ).fetchone()
        if sanction is not None and any(v is not None for v in sanction):
            checks.extend(self._sanction(u, *sanction))
        for (value,) in conn.execute(
            "SELECT threshold FROM annotation.statement_condition"
            " WHERE statement_id = %s AND threshold IS NOT NULL"
            " AND (cond_role = 'limit' OR measure_unit IS NOT NULL)",
            (statement_id,),
        ):
            have = numbers_in(u.quoted)
            ok = (
                int(Decimal(str(value))) in have
                if Decimal(str(value)) == int(Decimal(str(value)))
                else False
            )
            checks.append(
                {"what": "threshold", "value": str(value), "in_text": sorted(have), "ok": ok}
            )
        if not checks:
            return None
        verdict: Verdict = (
            "pass"
            if all(c["ok"] is True for c in checks)
            else ("fail" if any(c["ok"] is False for c in checks) else "inconclusive")
        )
        return GateResult(verdict, {"checks": checks})

    @staticmethod
    def _sanction(u: _Unit, amount_text: Any, low: Any, high: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if amount_text is not None:
            out.append(
                {
                    "what": "amount_text",
                    "value": amount_text,
                    "ok": str(amount_text) in f"{u.context}\n{u.text}",
                }
            )
        found = amounts(u.with_context)
        if not found and (low is not None or high is not None):
            stored_amounts = [None if v is None else int(v) for v in (low, high)]
            out.append({"what": "amounts", "stored": stored_amounts, "text": [], "ok": False})
        stored = (None if low is None else int(low), None if high is None else int(high))
        # 原文可能分級（一般與工商廠、場各有一組額度）：標註的下限與上限要是原文同一組。
        tiers = _ranges(found)
        want = next((t for t in tiers if t == stored), tiers[0] if tiers else (None, None))
        for name, have, wanted in (("min", stored[0], want[0]), ("max", stored[1], want[1])):
            if have is None and wanted is None:
                continue
            if have is None:
                out.append(
                    {"what": name, "stored": None, "text": wanted, "ok": None}
                )  # 原文有、標註沒有
            else:
                out.append({"what": name, "stored": have, "text": wanted, "ok": wanted == have})
        return out


# ---- 閘門二：引用 ------------------------------------------------------------------------------

_CITE = re.compile(rf"第([{NUMBER}]+)條(?:之([{NUMBER}]+))?(?:第([{NUMBER}]+)項)?")


def citations(text: str) -> list[str]:
    """原文引用的條（項）的路徑：「第七條第一項」→ art_7/para_1、「第五條之一」→ art_5-1。"""
    out: list[str] = []
    for m in _CITE.finditer(text):
        try:
            path = f"art_{cn_number(m.group(1))}"
            if m.group(2):
                path += f"-{cn_number(m.group(2))}"
            if m.group(3):
                path += f"/para_{cn_number(m.group(3))}"
        except ValueError:
            continue
        out.append(path)
    return out


@dataclass(frozen=True)
class ReferencesGate:
    """罰則所處罰的陳述所在的條（項），必須是原文引用的；而且那個陳述要先被接受。

    處罰一個沒被接受的義務沒有意義：它還在 `extracted` 時罰則無法判定，等下一輪；被駁回則罰則失敗。
    """

    method: str = "references"
    verifier: str = "gate:references@1"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        row = conn.execute(
            "SELECT pp.path, t.confirm_state::text FROM annotation.sanction AS sn"
            "  JOIN annotation.statement AS t ON t.id = sn.punishes_id"
            "  JOIN corpus.provision AS pp ON pp.id = t.provision_id"
            " WHERE sn.statement_id = %s",
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        u = _unit(conn, statement_id)
        target = str(row[0])
        cited = citations(u.text)
        covers = [c for c in cited if target == c or target.startswith(c + "/")]
        in_corpus = {
            str(p)
            for (p,) in conn.execute(
                "SELECT path FROM corpus.provision WHERE resource_id = %s AND path = ANY(%s)",
                (u.resource_id, cited),
            )
        }
        evidence = {
            "target": target,
            "cited": cited,
            "unresolved": [
                c for c in cited if c not in in_corpus
            ],  # 原文引用、語料沒有的：只記，不算錯
        }
        if not covers:
            return GateResult("fail", evidence)
        if row[1] == "rejected":
            return GateResult("fail", {**evidence, "reason": "the punished statement was rejected"})
        if row[1] == "extracted":
            return GateResult(
                "inconclusive",
                {**evidence, "reason": "the punished statement is not yet confirmed"},
            )
        return GateResult("pass", evidence)


def statement_as_of(conn: Conn, statement_id: int) -> date | None:
    """評估一個陳述用的日期：它所錨定的文字現行版本的最近一個生效起日；沒有則 None。"""
    row = conn.execute(
        "SELECT max(lower(v.force_period)) FROM annotation.statement AS s"
        "  JOIN corpus.provision_version AS v ON v.provision_id = s.provision_id"
        "   AND v.content_sha256 = s.content_sha256 AND v.superseded_at IS NULL"
        " WHERE s.id = %s",
        (statement_id,),
    ).fetchone()
    return None if row is None or row[0] is None else cast(date, row[0])


# ---- 閘門三：可執行 ----------------------------------------------------------------------------

_RAW_TRUE = {"le": 0, "lt": -1, "ge": 0, "gt": 1, "eq": 0, "ne": 1}
_RAW_FALSE = {"le": 1, "lt": 0, "ge": -1, "gt": 0, "eq": 1, "ne": 0}


@dataclass(frozen=True)
class _Cond:
    id: int
    role: str
    clause: int
    code: str
    comparator: str
    negated: bool
    threshold: Decimal | None
    delegated: bool
    by_concept: bool

    def value(self, truth: bool, resolved: Decimal) -> Decimal:
        """讓這個要件原子（含「非」）成立或不成立的事實值。"""
        raw_true = truth != self.negated
        delta = (_RAW_TRUE if raw_true else _RAW_FALSE)[self.comparator]
        return resolved + delta


# 義務內容成立、不成立時的結論。與 rules.lp 的 complies/2 同一張表：
# 禁止是義務的對極；許可兩邊都合規，不做所許可之事不是違反。
_POLARITY: dict[str, tuple[str, str]] = {
    "obligation": ("compliant", "violation"),
    "prohibition": ("violation", "compliant"),
    "permission": ("compliant", "compliant"),
}


@dataclass(frozen=True)
class ExecutableGate:
    """規範性陳述與罰則可被推論程式評估。規範性陳述的結論隨義務方向翻轉，適用範圍任一要件
    不成立則不適用。罰則在所處罰的陳述違反時：自己的適用範圍成立才列為候選，未知則附條件，
    不成立則不列。只檢查標註與推論程式之間，不檢查標註與原文之間。"""

    method: str = "executable"
    verifier: str = "gate:executable@2"
    language: str = "zho"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = _unit(conn, statement_id)
        if u.kind == "penalty":
            return self._penalty(conn, u)
        pair = _POLARITY.get(u.deontic or "")
        if u.kind != "prescriptive" or pair is None:
            return None
        ready = self._ready(conn, u)
        if isinstance(ready, GateResult):
            return ready
        as_of, conds, resolved, base = ready
        holds, fails = pair
        scope = [c for c in conds if c.role == "scope"]
        duty = [c for c in conds if c.role == "duty"]
        if not duty and holds != fails:
            return GateResult("inconclusive", {"reason": "no duty condition"})
        if not duty and not scope:
            return GateResult("inconclusive", {"reason": "no condition"})
        sat = self._vals(scope, True, resolved)
        cases: list[tuple[str, dict[str, float], str]] = []
        if duty:
            cases.append(("duty holds", base | sat | self._vals(duty, True, resolved), holds))
            cases.append(("duty broken", base | sat | self._vals(duty, False, resolved), fails))
        else:
            cases.append(("scope holds", base | sat, holds))
        duty_ok = self._vals(duty, holds == "compliant", resolved)
        for broken in scope:
            facts = (
                base
                | sat
                | duty_ok
                | {broken.code: float(broken.value(False, resolved[broken.id]))}
            )
            cases.append((f"{broken.code} not met", facts, "not_applicable"))
        return self._verdicts(conn, as_of, u.statement_id, cases)

    def _penalty(self, conn: Conn, u: _Unit) -> GateResult:
        row = conn.execute(
            "SELECT punishes_id FROM annotation.sanction WHERE statement_id = %s",
            (u.statement_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return GateResult("inconclusive", {"reason": "penalty punishes nothing"})
        punished = _unit(conn, cast(int, row[0]))
        pair = _POLARITY.get(punished.deontic or "")
        if punished.kind != "prescriptive" or pair is None or pair[0] == pair[1]:
            return GateResult("inconclusive", {"reason": "punished statement cannot be a breach"})
        breach_ready = self._ready(conn, punished)
        if isinstance(breach_ready, GateResult):
            return breach_ready
        _, pconds, presolved, pbase = breach_ready
        if not any(c.role == "duty" for c in pconds):
            return GateResult(
                "inconclusive", {"reason": "punished statement has no duty condition"}
            )
        pen_ready = self._ready(conn, u)
        if isinstance(pen_ready, GateResult):
            return pen_ready
        as_of, conds, resolved, base = pen_ready
        scope = [c for c in conds if c.role == "scope"]
        breach = (
            pbase
            | self._vals([c for c in pconds if c.role == "scope"], True, presolved)
            | self._vals([c for c in pconds if c.role == "duty"], pair[0] == "violation", presolved)
            | {"ap_intent": 1.0}
        )
        sat = self._vals(scope, True, resolved)
        held = base | breach | sat
        probe = evaluate(conn, as_of, held, self.language, include_unsettled=True)
        if probe.verdicts.get(punished.statement_id) != "violation":
            return GateResult(
                "inconclusive", {"reason": "breach facts did not violate the punished statement"}
            )
        cases: list[tuple[str, dict[str, float], str]] = [("scope holds", held, "candidate")]
        for broken in scope:
            facts = (
                base | breach | sat | {broken.code: float(broken.value(False, resolved[broken.id]))}
            )
            cases.append((f"{broken.code} not met", facts, "absent"))
        owned = next((c for c in scope if c.code not in breach), None)
        if owned is not None:
            unknown = {k: v for k, v in held.items() if k != owned.code}
            cases.append(("scope unknown", unknown, "conditional"))
        results: list[dict[str, Any]] = [
            {
                "case": "scope holds",
                "expected": "candidate",
                "got": self._penalty_state(probe, u.statement_id),
            }
        ]
        for name, facts, expected in cases[1:]:
            got = self._penalty_state(
                evaluate(conn, as_of, facts, self.language, include_unsettled=True),
                u.statement_id,
            )
            results.append({"case": name, "expected": expected, "got": got})
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    def _ready(
        self, conn: Conn, u: _Unit
    ) -> GateResult | tuple[date, list[_Cond], dict[int, Decimal], dict[str, float]]:
        as_of = self._as_of(conn, u)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        conds = self._conditions(conn, u.statement_id)
        problem = self._unsupported(conds)
        if problem:
            return GateResult("inconclusive", {"reason": problem})
        resolved: dict[int, Decimal] = {}
        for c in conds:
            value = c.threshold
            if value is None and c.delegated:
                row = conn.execute(
                    "SELECT annotation.threshold_at(%s, %s, %s)", (c.id, self.language, as_of)
                ).fetchone()
                value = None if row is None or row[0] is None else Decimal(str(row[0]))
            if value is None:
                return GateResult("inconclusive", {"reason": f"threshold of {c.code} unresolved"})
            resolved[c.id] = value
        return as_of, conds, resolved, self._exception_facts(conn, u.statement_id)

    def _verdicts(
        self,
        conn: Conn,
        as_of: date,
        statement_id: int,
        cases: list[tuple[str, dict[str, float], str]],
    ) -> GateResult:
        results: list[dict[str, Any]] = []
        for name, facts, expected in cases:
            result = evaluate(conn, as_of, facts, self.language, include_unsettled=True)
            results.append(
                {
                    "case": name,
                    "expected": expected,
                    "got": result.verdicts.get(statement_id),
                    "problems": [list(p) for p in result.problems],
                }
            )
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    @staticmethod
    def _vals(conds: list[_Cond], truth: bool, resolved: dict[int, Decimal]) -> dict[str, float]:
        return {c.code: float(c.value(truth, resolved[c.id])) for c in conds}

    @staticmethod
    def _penalty_state(result: Result, statement_id: int) -> str:
        hit = [p for p in result.penalties if p.statement_id == statement_id]
        if not hit:
            return "absent"
        conditional = {p.conditional for p in hit}
        if len(conditional) != 1:
            return "both"
        return "conditional" if hit[0].conditional else "candidate"

    def _as_of(self, conn: Conn, u: _Unit) -> date | None:
        return statement_as_of(conn, u.statement_id)

    @staticmethod
    def _conditions(conn: Conn, statement_id: int) -> list[_Cond]:
        rows = conn.execute(
            "SELECT c.id, c.cond_role::text, c.disjunct_no, k.code, c.comparator::text, c.negated,"
            "       c.threshold, c.delegate_provision_id IS NOT NULL,"
            "       c.value_concept_id IS NOT NULL"
            "  FROM annotation.statement_condition AS c"
            "  JOIN vocab.concept AS k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s AND c.cond_role <> 'limit' ORDER BY c.id",
            (statement_id,),
        ).fetchall()
        return [
            _Cond(
                cast(int, r[0]),
                cast(str, r[1]),
                cast(int, r[2]),
                cast(str, r[3]),
                cast(str, r[4]),
                bool(r[5]),
                None if r[6] is None else Decimal(str(r[6])),
                bool(r[7]),
                bool(r[8]),
            )
            for r in rows
        ]

    @staticmethod
    def _unsupported(conds: list[_Cond]) -> str | None:
        if any(c.by_concept for c in conds):
            return "a condition compares concept values"
        for role in ("scope", "duty"):
            if len({c.clause for c in conds if c.role == role}) > 1:
                return f"{role} has several clauses (disjunction)"
        codes = [c.code for c in conds]
        if len(codes) != len(set(codes)):
            return "two conditions share one concept"
        return None

    @staticmethod
    def _exception_facts(conn: Conn, statement_id: int) -> dict[str, float]:
        """這個陳述的但書：讓它們的要件不成立，才不會被排除。"""
        facts: dict[str, float] = {}
        rows = conn.execute(
            "SELECT c.comparator::text, c.negated, c.threshold, k.code"
            "  FROM annotation.statement_exception AS e"
            "  JOIN annotation.statement_condition AS c"
            "    ON c.statement_id = e.exception_id AND c.cond_role = 'scope'"
            "  JOIN vocab.concept AS k ON k.id = c.concept_id"
            " WHERE e.excluded_id = %s",
            (statement_id,),
        ).fetchall()
        for comparator, negated, threshold, code in rows:
            if threshold is None:
                continue
            c = _Cond(
                0,
                "scope",
                1,
                cast(str, code),
                cast(str, comparator),
                bool(negated),
                None,
                False,
                False,
            )
            facts[c.code] = float(c.value(False, Decimal(str(threshold))))
        return facts


DEFAULT_GATES: tuple[Gate, ...] = (NumbersGate(), ReferencesGate(), ExecutableGate())
