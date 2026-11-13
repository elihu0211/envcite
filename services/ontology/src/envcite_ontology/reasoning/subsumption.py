"""陳述進入推論後的涵攝評估：標準案例評估與指標量測。

將規範陳述作為大前提、案件事實作為小前提，呼叫推論引擎（clingo），
驗證三值 Kleene 涵攝邏輯（violation, compliant, conditional_violation, excluded, undetermined）、
缺漏事實識別、不確定概念需判斷事項、責任主體分流與行政罰法總則阻卻之正確性。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from envcite_ontology.db import Conn
from envcite_ontology.reasoning import facts
from envcite_ontology.reasoning.engine import evaluate


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
    measurements: facts.Measurements | None = None
    permits: facts.Permits | None = None
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


def evaluate_subsumption(
    conn: Conn,
    cases: Sequence[SubsumptionCase],
    *,
    id_resolver: Mapping[str, int] | Callable[[str], int] | None = None,
    language: str = "zho",
    include_unsettled: bool = False,
) -> SubsumptionReport:
    """執行整套涵攝測試案例並產出量測報告。"""

    def resolve_id(ref: str) -> int | None:
        if isinstance(ref, int):
            return ref
        if ref.isdigit():
            return int(ref)
        if id_resolver is None:
            return None
        if isinstance(id_resolver, Mapping):
            return id_resolver.get(ref)
        return id_resolver(ref)

    def resolve_label(num_id: int) -> str:
        if isinstance(id_resolver, Mapping):
            for k, v in id_resolver.items():
                if v == num_id:
                    return k
        return str(num_id)

    case_results: list[CaseResult] = []

    total_expected_verdicts = 0
    correct_verdicts = 0

    total_expected_missing = 0
    total_actual_missing = 0
    true_positive_missing = 0

    total_expected_judgements = 0
    total_actual_judgements = 0
    true_positive_judgements = 0

    total_expected_penalties = 0
    total_actual_penalties = 0
    true_positive_penalties = 0

    for c in cases:
        diffs: list[str] = []
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

        # 1. 判決比對
        actual_verdicts_by_label: dict[str, str] = {}
        for s_id, v in result.verdicts.items():
            label = resolve_label(s_id)
            actual_verdicts_by_label[label] = v
            actual_verdicts_by_label[str(s_id)] = v

        verdict_matches = True
        for ref, exp_v in c.expected_verdicts.items():
            total_expected_verdicts += 1
            act_v = actual_verdicts_by_label.get(ref)
            if act_v != exp_v:
                verdict_matches = False
                diffs.append(f"判決不符 [{ref}]: 預期 {exp_v}，實際 {act_v}")
            else:
                correct_verdicts += 1

        # 2. 缺事實比對
        actual_missing_set: set[tuple[str, str]] = set()
        for s_id, concept in result.missing:
            lbl = resolve_label(s_id)
            actual_missing_set.add((lbl, concept))
            actual_missing_set.add((str(s_id), concept))

        expected_missing_canon: set[tuple[str, str]] = set(c.expected_missing)
        # 精準比對（以 label 或 id）
        matched_missing = {m for m in expected_missing_canon if m in actual_missing_set}
        total_expected_missing += len(expected_missing_canon)
        # 實際缺事實計數（過濾別名重複計入）
        actual_missing_canon = {(resolve_label(s_id), concept) for s_id, concept in result.missing}
        total_actual_missing += len(actual_missing_canon)
        true_positive_missing += len(matched_missing)

        missing_matches = matched_missing == expected_missing_canon and len(
            actual_missing_canon
        ) == len(expected_missing_canon)
        if not missing_matches:
            diffs.append(
                f"缺事實不符: 預期 {sorted(expected_missing_canon)}，"
                f"實際 {sorted(actual_missing_canon)}"
            )

        # 3. 需判斷比對
        actual_judgements_set: set[tuple[str, str]] = set()
        for s_id, concept in result.needs_judgement:
            lbl = resolve_label(s_id)
            actual_judgements_set.add((lbl, concept))
            actual_judgements_set.add((str(s_id), concept))

        expected_judgements_canon: set[tuple[str, str]] = set(c.expected_judgements)
        matched_judgements = {j for j in expected_judgements_canon if j in actual_judgements_set}
        total_expected_judgements += len(expected_judgements_canon)
        actual_judgements_canon = {
            (resolve_label(s_id), concept) for s_id, concept in result.needs_judgement
        }
        total_actual_judgements += len(actual_judgements_canon)
        true_positive_judgements += len(matched_judgements)

        judgement_matches = matched_judgements == expected_judgements_canon and len(
            actual_judgements_canon
        ) == len(expected_judgements_canon)
        if not judgement_matches:
            diffs.append(
                f"需判斷不符: 預期 {sorted(expected_judgements_canon)}，"
                f"實際 {sorted(actual_judgements_canon)}"
            )

        # 4. 處罰效果比對
        actual_penalties_set: set[tuple[str, str, bool]] = set()
        actual_penalties_canon: set[tuple[str, str, bool]] = set()
        for p in result.penalties:
            lbl = resolve_label(p.statement_id)
            actual_penalties_set.add((lbl, p.effect, p.conditional))
            actual_penalties_set.add((str(p.statement_id), p.effect, p.conditional))
            actual_penalties_canon.add((lbl, p.effect, p.conditional))

        expected_penalties_canon: set[tuple[str, str, bool]] = set(c.expected_penalties)
        matched_penalties = {p for p in expected_penalties_canon if p in actual_penalties_set}
        total_expected_penalties += len(expected_penalties_canon)
        total_actual_penalties += len(actual_penalties_canon)
        true_positive_penalties += len(matched_penalties)

        penalty_matches = matched_penalties == expected_penalties_canon and len(
            actual_penalties_canon
        ) == len(expected_penalties_canon)
        if not penalty_matches:
            diffs.append(
                f"罰則候選不符: 預期 {sorted(expected_penalties_canon)}，"
                f"實際 {sorted(actual_penalties_canon)}"
            )

        # 5. 總則比對（不予處罰與時效）
        actual_unpunished_canon = {resolve_label(s_id) for s_id, _ in result.general.unpunished}
        actual_time_barred_canon = {resolve_label(s_id) for s_id in result.general.time_barred}
        expected_unpunished_canon = set(c.expected_unpunished)
        expected_time_barred_canon = set(c.expected_time_barred)

        general_matches = expected_unpunished_canon.issubset(
            actual_unpunished_canon
        ) and expected_time_barred_canon.issubset(actual_time_barred_canon)
        if not general_matches:
            diffs.append(
                f"總則狀態不符: 預期不罰 {sorted(expected_unpunished_canon)}"
                f"（實際 {sorted(actual_unpunished_canon)}）；"
                f"預期時效 {sorted(expected_time_barred_canon)}"
                f"（實際 {sorted(actual_time_barred_canon)}）"
            )

        passed = (
            verdict_matches
            and penalty_matches
            and missing_matches
            and judgement_matches
            and general_matches
        )

        case_results.append(
            CaseResult(
                case_id=c.case_id,
                title=c.title,
                passed=passed,
                verdict_matches=verdict_matches,
                penalty_matches=penalty_matches,
                missing_matches=missing_matches,
                judgement_matches=judgement_matches,
                general_matches=general_matches,
                actual_verdicts={
                    k: v for k, v in actual_verdicts_by_label.items() if not k.isdigit()
                },
                actual_penalties=tuple(sorted(actual_penalties_canon)),
                actual_missing=tuple(sorted(actual_missing_canon)),
                actual_judgements=tuple(sorted(actual_judgements_canon)),
                actual_unpunished=tuple(sorted(actual_unpunished_canon)),
                actual_time_barred=tuple(sorted(actual_time_barred_canon)),
                diffs=tuple(diffs),
            )
        )

    passed_count = sum(1 for r in case_results if r.passed)
    total_cases = len(cases)

    return SubsumptionReport(
        total_cases=total_cases,
        passed_cases=passed_count,
        case_pass_rate=_safe_div(passed_count, total_cases),
        verdict_accuracy=_safe_div(correct_verdicts, total_expected_verdicts),
        missing_precision=_safe_div(true_positive_missing, total_actual_missing),
        missing_recall=_safe_div(true_positive_missing, total_expected_missing),
        judgement_precision=_safe_div(true_positive_judgements, total_actual_judgements),
        judgement_recall=_safe_div(true_positive_judgements, total_expected_judgements),
        penalty_precision=_safe_div(true_positive_penalties, total_actual_penalties),
        penalty_recall=_safe_div(true_positive_penalties, total_expected_penalties),
        results=tuple(case_results),
    )
