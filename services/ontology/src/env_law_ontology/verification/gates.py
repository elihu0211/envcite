"""驗證閘門：每個閘門檢查標註的一個性質，回報通過、失敗、無法判定，或不適用。

閘門檢查的是「標註與原文、與推論程式是否一致」，不是「法律上對不對」。這裡的三個都是確定性的
工具，不呼叫語言模型：

- `numbers`：罰則的額度與帶單位的門檻值，必須與原文的數字一致（中文數字與阿拉伯數字都讀）。
- `references`：罰則所處罰的陳述，必須是原文引用的那一條（項）。
- `executable`：標註可被推論程式評估，而且在門檻值兩側、適用範圍的兩側，結論如條件所預期地翻轉。

它們互相獨立，也獨立於抽取者；但沒有一個能證明要件的語意、陳述的種類或義務方向忠於原文——那需要
往返驗證、獨立測試或跨家族評審（設計見 02-05 §5.9），它們要呼叫模型，不在這裡。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol, cast

import psycopg

from env_law_ontology.reasoning import evaluate

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

    @property
    def quoted(self) -> str:
        return self.text if self.span is None else self.text[self.span[0] : self.span[1]]


def _unit(conn: Conn, statement_id: int) -> _Unit:
    row = conn.execute(
        "SELECT s.provision_id, p.path, p.resource_id, v.content, lower(s.span), upper(s.span),"
        "       s.kind::text, s.deontic::text"
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
            " AND (role = 'limit' OR unit IS NOT NULL)",
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
                {"what": "amount_text", "value": amount_text, "ok": str(amount_text) in u.text}
            )
        found = amounts(u.quoted)
        want_low = next((v for v, q in found if q == "以上"), None)
        want_high = next((v for v, q in found if q == "以下"), None)
        if not found and (low is not None or high is not None):
            out.append({"what": "amounts", "stored": [low, high], "text": [], "ok": False})
        for name, stored, want in (("min", low, want_low), ("max", high, want_high)):
            if stored is None and want is None:
                continue
            if stored is None:
                out.append(
                    {"what": name, "stored": None, "text": want, "ok": None}
                )  # 原文有、標註沒有
            else:
                out.append(
                    {"what": name, "stored": int(stored), "text": want, "ok": want == int(stored)}
                )
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
            "SELECT pp.path, t.review::text FROM annotation.sanction AS sn"
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
        "SELECT max(lower(v.in_force)) FROM annotation.statement AS s"
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


@dataclass(frozen=True)
class ExecutableGate:
    """義務陳述可被推論程式評估，且結論如要件所預期地翻轉：兩個門檻值之內合規、之外違反，
    適用範圍的任一要件不成立則不適用。只檢查標註與推論程式之間，不檢查標註與原文之間。"""

    method: str = "executable"
    verifier: str = "gate:executable@1"
    language: str = "zho"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = _unit(conn, statement_id)
        if not (u.kind == "prescriptive" and u.deontic == "obligation"):
            return None
        as_of = self._as_of(conn, u)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        conds = self._conditions(conn, statement_id)
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
        scope = [c for c in conds if c.role == "scope"]
        duty = [c for c in conds if c.role == "duty"]
        if not duty:
            return GateResult("inconclusive", {"reason": "no duty condition"})
        base = self._exception_facts(conn, statement_id)
        cases: list[tuple[str, dict[str, float], str]] = []
        sat = {c.code: float(c.value(True, resolved[c.id])) for c in scope}
        cases.append(
            (
                "duty holds",
                base | sat | {c.code: float(c.value(True, resolved[c.id])) for c in duty},
                "compliant",
            )
        )
        cases.append(
            (
                "duty broken",
                base | sat | {c.code: float(c.value(False, resolved[c.id])) for c in duty},
                "violation",
            )
        )
        duty_ok = {c.code: float(c.value(True, resolved[c.id])) for c in duty}
        for broken in scope:
            facts = (
                base
                | sat
                | duty_ok
                | {broken.code: float(broken.value(False, resolved[broken.id]))}
            )
            cases.append((f"{broken.code} not met", facts, "not_applicable"))
        results: list[dict[str, Any]] = []
        for name, facts, expected in cases:
            r = evaluate(conn, as_of, facts, self.language, include_unsettled=True)
            got = r.verdicts.get(statement_id)
            results.append(
                {
                    "case": name,
                    "expected": expected,
                    "got": got,
                    "problems": [list(p) for p in r.problems],
                }
            )
        failed = [x for x in results if x["got"] != x["expected"]]
        return GateResult(
            "fail" if failed else "pass", {"as_of": as_of.isoformat(), "cases": results}
        )

    def _as_of(self, conn: Conn, u: _Unit) -> date | None:
        return statement_as_of(conn, u.statement_id)

    @staticmethod
    def _conditions(conn: Conn, statement_id: int) -> list[_Cond]:
        rows = conn.execute(
            "SELECT c.id, c.role::text, c.clause, k.code, c.comparator::text, c.negated,"
            "       c.threshold, c.delegate_provision_id IS NOT NULL,"
            "       c.value_concept_id IS NOT NULL"
            "  FROM annotation.statement_condition AS c"
            "  JOIN vocab.concept AS k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s AND c.role <> 'limit' ORDER BY c.id",
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
            "    ON c.statement_id = e.exception_id AND c.role = 'scope'"
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
