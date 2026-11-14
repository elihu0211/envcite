"""陳述進入推論後的涵攝評估：標準案例評估與指標量測。

將規範陳述作為大前提、案件事實作為小前提，呼叫推論引擎（clingo），
驗證三值 Kleene 涵攝邏輯（violation, compliant, conditional_violation, excluded, undetermined）、
缺漏事實識別、不確定概念需判斷事項、責任主體分流與行政罰法總則阻卻之正確性。
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from envcite_ontology.db import Conn
from envcite_ontology.reasoning.engine import Penalty, Result, evaluate
from envcite_ontology.reasoning.facts.case_inputs import Measurements, Permits


@dataclass(frozen=True)
class SubsumptionCase:
    """單一法學涵攝評估案例。"""

    case_id: str
    title: str
    as_of: date
    case_facts: Mapping[str, Any]
    expected_verdicts: Mapping[str, str]
    expected_penalties: tuple[tuple[str, str, bool], ...] = ()
    expected_missing: tuple[tuple[str, str], ...] = ()
    expected_judgements: tuple[tuple[str, str], ...] = ()
    expected_unpunished: tuple[str, ...] = ()
    expected_time_barred: tuple[str, ...] = ()
    decided_on: date | None = None
    measurements: Measurements | None = None
    permits: Permits | None = None
    actor_bases: tuple[str, ...] | None = None
    description: str = ""


@dataclass(frozen=True)
class CaseResult:
    """單一案例評估比對結果。"""

    case_id: str
    title: str
    passed: bool
    verdict_matches: bool
    penalty_matches: bool
    missing_matches: bool
    judgement_matches: bool
    general_matches: bool
    actual_verdicts: Mapping[str, str]
    actual_penalties: tuple[tuple[str, str, bool], ...]
    actual_missing: tuple[tuple[str, str], ...]
    actual_judgements: tuple[tuple[str, str], ...]
    actual_unpunished: tuple[str, ...]
    actual_time_barred: tuple[str, ...]
    diffs: tuple[str, ...]


@dataclass(frozen=True)
class SubsumptionReport:
    """涵攝評估整體報告與客觀度量指標。"""

    total_cases: int
    passed_cases: int
    case_pass_rate: float
    verdict_accuracy: float
    missing_precision: float
    missing_recall: float
    judgement_precision: float
    judgement_recall: float
    penalty_precision: float
    penalty_recall: float
    results: tuple[CaseResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """報告轉成巢狀的 dict（含各案例結果），供寫成 JSON。"""
        return asdict(self)

    def to_markdown(self) -> str:
        """報告轉成 Markdown：總覽指標、案例明細表，與失敗案例的差異。"""
        cpr = self.case_pass_rate * 100
        mp, mr = self.missing_precision * 100, self.missing_recall * 100
        jp, jr = self.judgement_precision * 100, self.judgement_recall * 100
        pp, pr = self.penalty_precision * 100, self.penalty_recall * 100
        lines = [
            "# 陳述進入推論後的涵攝評估報告（Subsumption Evaluation Report）",
            "",
            f"- **評估案例總數**：{self.total_cases}",
            f"- **完全通過案例數**：{self.passed_cases}（通過率：{cpr:.1f}%）",
            f"- **三值判決準確率（Verdict Accuracy）**：{self.verdict_accuracy * 100:.1f}%",
            f"- **缺事實識別指標（Missing Facts）**：Precision = {mp:.1f}%, Recall = {mr:.1f}%",
            f"- **不確定概念判斷指標（Judgements）**：Precision = {jp:.1f}%, Recall = {jr:.1f}%",
            f"- **處罰效果候選指標（Penalties）**：Precision = {pp:.1f}%, Recall = {pr:.1f}%",
            "",
            "## 案例明細表",
            "",
            "| 案例編號 | 標題 | 判決符合 | 罰則符合 | 缺事實符合 | 需判斷符合 | 總則符合 | 通過 |",
            "|---|---|:---:|:---:|:---:|:---:|:---:|:---:|",
        ]
        for r in self.results:
            status = "✓" if r.passed else "✗"
            v = "✓" if r.verdict_matches else "✗"
            p = "✓" if r.penalty_matches else "✗"
            m = "✓" if r.missing_matches else "✗"
            j = "✓" if r.judgement_matches else "✗"
            g = "✓" if r.general_matches else "✗"
            lines.append(f"| {r.case_id} | {r.title} | {v} | {p} | {m} | {j} | {g} | {status} |")

        failed = [r for r in self.results if not r.passed]
        if failed:
            lines.extend(["", "## 失敗案例差異詳情", ""])
            for r in failed:
                lines.append(f"### {r.case_id}: {r.title}")
                for d in r.diffs:
                    lines.append(f"- {d}")
                lines.append("")
        return "\n".join(lines)


def _safe_div(numerator: int, denominator: int) -> float:
    return float(numerator) / denominator if denominator > 0 else 1.0


@dataclass(frozen=True, slots=True)
class _Match[Item: tuple[str | bool, ...]]:
    """某類結果的預期與實際比對；實際已把別名（標籤與數字編號）合為一筆。"""

    expected: frozenset[Item]
    actual: frozenset[Item]
    matched: int

    @property
    def passed(self) -> bool:
        return self.matched == len(self.expected) == len(self.actual)

    @property
    def counts(self) -> tuple[int, int, int]:
        return len(self.expected), len(self.actual), self.matched

    def diffs(self, title: str) -> list[str]:
        if self.passed:
            return []
        return [f"{title}不符: 預期 {sorted(self.expected)}，實際 {sorted(self.actual)}"]


@dataclass(frozen=True, slots=True)
class _General:
    """總則（不予處罰與時效）的預期與實際；預期只需是實際的子集。"""

    expected_unpunished: frozenset[str]
    expected_time_barred: frozenset[str]
    actual_unpunished: frozenset[str]
    actual_time_barred: frozenset[str]

    @property
    def passed(self) -> bool:
        return self.expected_unpunished <= self.actual_unpunished and (
            self.expected_time_barred <= self.actual_time_barred
        )

    def diffs(self) -> list[str]:
        if self.passed:
            return []
        return [
            f"總則狀態不符: 預期不罰 {sorted(self.expected_unpunished)}"
            f"（實際 {sorted(self.actual_unpunished)}）；"
            f"預期時效 {sorted(self.expected_time_barred)}"
            f"（實際 {sorted(self.actual_time_barred)}）"
        ]


@dataclass(slots=True)
class _Tally:
    """跨案例累計的預期筆數、實際筆數與命中筆數。"""

    expected: int = 0
    actual: int = 0
    matched: int = 0

    def add(self, expected: int, actual: int, matched: int) -> None:
        self.expected += expected
        self.actual += actual
        self.matched += matched

    @property
    def precision(self) -> float:
        return _safe_div(self.matched, self.actual)

    @property
    def recall(self) -> float:
        return _safe_div(self.matched, self.expected)


@dataclass(slots=True)
class _Totals:
    """各類結果的累計；判決只用命中 / 預期（準確率），不用實際筆數。"""

    verdicts: _Tally = field(default_factory=_Tally)
    missing: _Tally = field(default_factory=_Tally)
    judgements: _Tally = field(default_factory=_Tally)
    penalties: _Tally = field(default_factory=_Tally)


def _labeler(id_resolver: Mapping[str, int] | Callable[[str], int] | None) -> Callable[[int], str]:
    """陳述編號 → 解析器裡的標籤；對不到（或解析器不是對照表）則為編號本身。"""

    def label(num_id: int) -> str:
        if isinstance(id_resolver, Mapping):
            for k, v in id_resolver.items():
                if v == num_id:
                    return k
        return str(num_id)

    return label


def _verdicts_by_label(label: Callable[[int], str], verdicts: Mapping[int, str]) -> dict[str, str]:
    by_label: dict[str, str] = {}
    for s_id, v in verdicts.items():
        by_label[label(s_id)] = v
        by_label[str(s_id)] = v
    return by_label


def _verdict_diffs(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    return [
        f"判決不符 [{ref}]: 預期 {exp_v}，實際 {actual.get(ref)}"
        for ref, exp_v in expected.items()
        if actual.get(ref) != exp_v
    ]


def _match_pairs(
    label: Callable[[int], str],
    expected: Iterable[tuple[str, str]],
    found: Iterable[tuple[int, str]],
) -> _Match[tuple[str, str]]:
    """（陳述, 概念）的比對：預期可用標籤或數字編號指稱陳述。"""
    actual = {(label(s_id), concept) for s_id, concept in found}
    aliased = actual | {(str(s_id), concept) for s_id, concept in found}
    expected_set = frozenset(expected)
    return _Match(expected_set, frozenset(actual), len(expected_set & aliased))


def _match_penalties(
    label: Callable[[int], str],
    expected: Iterable[tuple[str, str, bool]],
    found: Iterable[Penalty],
) -> _Match[tuple[str, str, bool]]:
    actual = {(label(p.statement_id), p.effect, p.conditional) for p in found}
    aliased = actual | {(str(p.statement_id), p.effect, p.conditional) for p in found}
    expected_set = frozenset(expected)
    return _Match(expected_set, frozenset(actual), len(expected_set & aliased))


def _grade(
    c: SubsumptionCase, result: Result, label: Callable[[int], str], totals: _Totals
) -> CaseResult:
    """一個案例的推論結果與預期比對，並把命中筆數累計進 `totals`。"""
    by_label = _verdicts_by_label(label, result.verdicts)
    verdict_diffs = _verdict_diffs(c.expected_verdicts, by_label)
    missing = _match_pairs(label, c.expected_missing, result.missing)
    judgements = _match_pairs(label, c.expected_judgements, result.needs_judgement)
    penalties = _match_penalties(label, c.expected_penalties, result.penalties)
    general = _General(
        expected_unpunished=frozenset(c.expected_unpunished),
        expected_time_barred=frozenset(c.expected_time_barred),
        actual_unpunished=frozenset(label(s_id) for s_id, _ in result.general.unpunished),
        actual_time_barred=frozenset(label(s_id) for s_id in result.general.time_barred),
    )
    expected_verdicts = len(c.expected_verdicts)
    totals.verdicts.add(expected_verdicts, 0, expected_verdicts - len(verdict_diffs))
    totals.missing.add(*missing.counts)
    totals.judgements.add(*judgements.counts)
    totals.penalties.add(*penalties.counts)
    verdict_matches = not verdict_diffs
    return CaseResult(
        case_id=c.case_id,
        title=c.title,
        passed=(
            verdict_matches
            and penalties.passed
            and missing.passed
            and judgements.passed
            and general.passed
        ),
        verdict_matches=verdict_matches,
        penalty_matches=penalties.passed,
        missing_matches=missing.passed,
        judgement_matches=judgements.passed,
        general_matches=general.passed,
        actual_verdicts={k: v for k, v in by_label.items() if not k.isdigit()},
        actual_penalties=tuple(sorted(penalties.actual)),
        actual_missing=tuple(sorted(missing.actual)),
        actual_judgements=tuple(sorted(judgements.actual)),
        actual_unpunished=tuple(sorted(general.actual_unpunished)),
        actual_time_barred=tuple(sorted(general.actual_time_barred)),
        diffs=(
            *verdict_diffs,
            *missing.diffs("缺事實"),
            *judgements.diffs("需判斷"),
            *penalties.diffs("罰則候選"),
            *general.diffs(),
        ),
    )


def evaluate_subsumption(
    conn: Conn,
    cases: Sequence[SubsumptionCase],
    *,
    id_resolver: Mapping[str, int] | Callable[[str], int] | None = None,
    language: str = "zho",
    include_unsettled: bool = False,
) -> SubsumptionReport:
    """執行整套涵攝測試案例並產出量測報告。"""
    label = _labeler(id_resolver)
    totals = _Totals()
    case_results: list[CaseResult] = []
    for c in cases:
        result = evaluate(
            conn,
            c.as_of,
            c.case_facts,
            language=language,
            include_unsettled=include_unsettled,
            decided_on=c.decided_on,
            measurements=c.measurements,
            permits=c.permits,
            actor_bases=c.actor_bases,
        )
        case_results.append(_grade(c, result, label, totals))

    passed_count = sum(1 for r in case_results if r.passed)
    total_cases = len(cases)

    return SubsumptionReport(
        total_cases=total_cases,
        passed_cases=passed_count,
        case_pass_rate=_safe_div(passed_count, total_cases),
        verdict_accuracy=totals.verdicts.recall,
        missing_precision=totals.missing.precision,
        missing_recall=totals.missing.recall,
        judgement_precision=totals.judgements.precision,
        judgement_recall=totals.judgements.recall,
        penalty_precision=totals.penalties.precision,
        penalty_recall=totals.penalties.recall,
        results=tuple(case_results),
    )
