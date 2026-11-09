"""端到端作答結果的彙總：只計數輸入 run 裡已有的分類，不產生任何輸入檔沒有的數字。

輸入是 `tools/grounding_check_eval.py` 寫出的逐題結果（`results`）與該次執行的 `meta`。每題的
`final` 分類、`cites_expected`、`evidence_has_expected` 都由那支工具對真實服務與 qrels 判定；本模組
不判定對錯，也沒有計時：延遲只能來自真實量測（`tools/load_bench.py`）。

口徑與已有的判定一致：

- 無答案題給出有引用回答的比例是**上界**，不是不當作答率。放過的題要逐題檢視才知道（題庫標註錯、
  澄清題與灰區都在其中）。
- 有引用但未引到期望條的比例**無法判斷好壞**：期望條是 qrels 裡判為相關的條，qrels 沒有判定的條
  是洞，不是答錯。
- 兩者分母不同、意義不同，所以不合算成單一比率。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

NEGATIVE_BUCKET = "negative"

FINAL_REFUSED_NO_EVIDENCE = "refused_no_evidence"  # 檢索無任何結果
FINAL_REFUSED_BELOW_FLOOR = "refused_below_floor"  # 有結果但皆未達下界
FINAL_REFUSED_LOCAL_LAW = "refused_local_law_uncovered"  # 問地方自治法規，且無條文達下界
FINAL_MODEL_INSUFFICIENT = "model_insufficient"  # 模型回「證據不足」
FINAL_VOID_NO_CITATION = "void_no_citation"  # 回答沒有任何代號
FINAL_VOID_UNKNOWN_CITATION = "void_unknown_citation"  # 引用了證據裡沒有的代號
FINAL_VOID_EMPTY = "void_empty"  # 模型沒有產生回答
FINAL_ANSWERED = "answered"  # 有引用的回答
FINAL_MODEL_ERROR = "model_error"  # 模型端點失敗（重試用盡）；不歸入任何一類

FLOOR_REFUSALS = frozenset(
    {FINAL_REFUSED_NO_EVIDENCE, FINAL_REFUSED_BELOW_FLOOR, FINAL_REFUSED_LOCAL_LAW}
)
MODEL_REFUSALS = frozenset({FINAL_MODEL_INSUFFICIENT})
VERIFY_VOIDS = frozenset({FINAL_VOID_NO_CITATION, FINAL_VOID_UNKNOWN_CITATION, FINAL_VOID_EMPTY})
KNOWN_FINALS = FLOOR_REFUSALS | MODEL_REFUSALS | VERIFY_VOIDS | {FINAL_ANSWERED, FINAL_MODEL_ERROR}

# 報表的列順序。
FINALS = (
    FINAL_REFUSED_NO_EVIDENCE,
    FINAL_REFUSED_BELOW_FLOOR,
    FINAL_REFUSED_LOCAL_LAW,
    FINAL_MODEL_INSUFFICIENT,
    FINAL_VOID_NO_CITATION,
    FINAL_VOID_UNKNOWN_CITATION,
    FINAL_VOID_EMPTY,
    FINAL_ANSWERED,
    FINAL_MODEL_ERROR,
)

# consult `Refused.Reason`（按列舉名）→ 該題的 final。與契約列舉的完整對應由
# tests/test_refusal_reasons.py 對 proto 產生碼守門：契約新增原因而這裡沒跟上，測試失敗。
REFUSAL_FINAL = {
    "REASON_NO_EVIDENCE": FINAL_REFUSED_NO_EVIDENCE,
    "REASON_OUT_OF_SCOPE": FINAL_REFUSED_BELOW_FLOOR,
    "REASON_LOCAL_LAW_UNCOVERED": FINAL_REFUSED_LOCAL_LAW,
}
# 評估題庫不會觸發的原因：出現就是題庫或服務出錯，工具要停下，不歸類。
REFUSAL_UNEXPECTED = frozenset({"REASON_UNSPECIFIED", "REASON_MISSING_INPUT"})


class InputError(ValueError):
    """輸入的逐題結果不合格式：不猜、不略過，整份拒絕。"""


@dataclass(frozen=True)
class Outcomes:
    """一組題目的最後分類計數。"""

    total: int
    floor_refused: int
    model_insufficient: int
    verify_void: int
    answered: int  # 給出有引用的回答
    model_error: int

    @property
    def not_answered(self) -> int:
        """最後沒有回答（下界、模型自認不足、引用查核作廢）；模型端點失敗不算拒答。"""
        return self.floor_refused + self.model_insufficient + self.verify_void

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "not_answered": self.not_answered}


@dataclass(frozen=True)
class CitationMetrics:
    """可答題中，所引條文與期望條（qrels 判為相關的條）的關係。"""

    answerable_total: int
    answered: int
    cites_expected: int
    cites_other: int  # 有引用但未引到任何期望條；無法判斷好壞

    # 過下界的可答題（封閉證據集非空）中，證據集含期望條的題數與其中最後未作答的題數
    passed_floor: int
    evidence_has_expected: int
    evidence_has_expected_not_answered: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnswerQualitySummary:
    total_questions: int
    negative: Outcomes
    answerable: Outcomes
    citation: CitationMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_questions": self.total_questions,
            "negative": self.negative.to_dict(),
            "answerable": self.answerable.to_dict(),
            "citation": self.citation.to_dict(),
        }


def _outcomes(rows: Sequence[Mapping[str, Any]]) -> Outcomes:
    c = Counter(r["final"] for r in rows)
    return Outcomes(
        total=len(rows),
        floor_refused=sum(c[k] for k in FLOOR_REFUSALS),
        model_insufficient=sum(c[k] for k in MODEL_REFUSALS),
        verify_void=sum(c[k] for k in VERIFY_VOIDS),
        answered=c[FINAL_ANSWERED],
        model_error=c[FINAL_MODEL_ERROR],
    )


def _validate(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows = list(records)
    for r in rows:
        ident = r.get("id", "?")
        final = r.get("final")
        if final not in KNOWN_FINALS:
            raise InputError(f"{ident}: 不認得的 final 分類 {final!r}")
        if r.get("bucket") is None:
            raise InputError(f"{ident}: 缺 bucket")
        if (
            r["bucket"] != NEGATIVE_BUCKET
            and final == FINAL_ANSWERED
            and not isinstance(r.get("cites_expected"), bool)
        ):
            # 可答題的有引用回答必有 cites_expected；缺了就無從分到「含期望條」或「不含」
            raise InputError(f"{ident}: 可答題的 answered 缺 cites_expected")
    return rows


def evaluate_records(records: Iterable[Mapping[str, Any]]) -> AnswerQualitySummary:
    """彙總逐題結果。分類不認得、可答題缺判定欄位時拋 `InputError`。"""
    rows = _validate(records)
    neg = [r for r in rows if r["bucket"] == NEGATIVE_BUCKET]
    pos = [r for r in rows if r["bucket"] != NEGATIVE_BUCKET]
    answerable = _outcomes(pos)

    cites_expected = sum(
        1 for r in pos if r["final"] == FINAL_ANSWERED and r["cites_expected"] is True
    )
    passed = [r for r in pos if r["final"] not in FLOOR_REFUSALS]
    has_expected = [r for r in passed if r.get("evidence_has_expected") is True]
    citation = CitationMetrics(
        answerable_total=answerable.total,
        answered=answerable.answered,
        cites_expected=cites_expected,
        cites_other=answerable.answered - cites_expected,
        passed_floor=len(passed),
        evidence_has_expected=len(has_expected),
        evidence_has_expected_not_answered=sum(
            1 for r in has_expected if r["final"] != FINAL_ANSWERED
        ),
    )
    return AnswerQualitySummary(
        total_questions=len(rows),
        negative=_outcomes(neg),
        answerable=answerable,
        citation=citation,
    )


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "—"


def _provenance(meta: Mapping[str, Any], subset: str | None) -> list[str]:
    """輸入 run 的來源：commit、時間、題庫與語料快照。報告本身沒有自己的時間。"""
    commit = meta.get("envcite_commit")
    dirty = "（工作樹有未提交變更）" if meta.get("git_dirty") else ""
    golden = meta.get("golden") or {}
    split = meta.get("split") or {}
    snaps = "；".join(f"{s['feed']} {s['data_date']}" for s in meta.get("snapshots") or [])
    calls = meta.get("model_calls")
    lines = [
        f"- 輸入 run：{meta.get('run_at', '未記')}；commit `{commit}`{dirty}",
        f"- 輸入檔：`{meta.get('input_name', '未記')}` sha256 `{meta.get('input_sha256', '未記')}`",
        f"- 題庫：`{golden.get('path', '未記')}` sha256 `{golden.get('sha256', '未記')}`",
        f"- 語料快照：{snaps or '未記'}；as_of {meta.get('as_of', '未記')}",
        f"- 模型：`{meta.get('model', '未記')}`；模型呼叫 {'未記' if calls is None else calls} 次",
    ]
    if split:
        lines.append(f"- 輸入 run 的切分：`{split.get('path')}` 子集 `{split.get('subset')}`")
    if subset:
        lines.append(f"- 本報告另以切分檔篩出子集：`{subset}`")
    return lines


def format_markdown_report(
    summary: AnswerQualitySummary,
    run_meta: Mapping[str, Any],
    subset: str | None = None,
) -> str:
    """Markdown 報告：檔頭記輸入 run 的來源，內文只有計數與比例。"""
    n, a, c = summary.negative, summary.answerable, summary.citation
    lines = [
        "# 作答結果彙總",
        "",
        "重新彙總輸入 run 的逐題分類，沒有重跑任何檢索或模型，也沒有延遲（延遲見"
        " `load-bench-*`）。",
        "",
        *_provenance(run_meta, subset),
        f"- 題數：{summary.total_questions}（無答案 {n.total}、可答 {a.total}）",
        "",
        "## 1. 最後分類",
        "",
        "| 題目類型 | 題數 | 下界攔截 | 模型自認不足 | 引用查核作廢 | 給出有引用的回答 |"
        " 模型端點失敗 |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, o in (("無答案題", n), ("可答題", a)):
        lines.append(
            f"| {label} | {o.total} | {o.floor_refused} ({_pct(o.floor_refused, o.total)}) |"
            f" {o.model_insufficient} ({_pct(o.model_insufficient, o.total)}) |"
            f" {o.verify_void} ({_pct(o.verify_void, o.total)}) |"
            f" {o.answered} ({_pct(o.answered, o.total)}) |"
            f" {o.model_error} ({_pct(o.model_error, o.total)}) |"
        )
    lines += [
        "",
        f"- **無答案題給出有引用回答的比例（上界）**：{n.answered}／{n.total}"
        f"（{_pct(n.answered, n.total)}）。這不是不當作答率：被放過的題要逐題檢視"
        "（題庫標註錯、澄清題、灰區都可能在其中；做法與一次結果見"
        " `eval/grounding-check-leaked-2026-11-03.md`）。",
        f"- 無答案題最後拒答（下界、模型自認不足、引用查核作廢）：{n.not_answered}／{n.total}"
        f"（{_pct(n.not_answered, n.total)}）。",
        f"- 可答題最後沒有回答：{a.not_answered}／{a.total}（{_pct(a.not_answered, a.total)}）；"
        "其中下界攔截與模型自認不足各是檢索與模型的保守，不是對錯。",
        "",
        "## 2. 可答題的引用與期望條",
        "",
        f"- **有引用但未引到期望條的比例（無法判斷好壞）**：{c.cites_other}／{c.answered}"
        f"（{_pct(c.cites_other, c.answered)}）。期望條是 qrels 判為相關的條；所引的條在 qrels"
        "沒有判定是洞，不是答錯。",
        f"- 有引用的回答中，所引條文含期望條：{c.cites_expected}／{c.answered}"
        f"（{_pct(c.cites_expected, c.answered)}）；占全部可答題 {c.cites_expected}／"
        f"{c.answerable_total}（{_pct(c.cites_expected, c.answerable_total)}）。",
        f"- 過下界的可答題 {c.passed_floor} 題中，封閉證據集含期望條：{c.evidence_has_expected}"
        f"（{_pct(c.evidence_has_expected, c.passed_floor)}）；其中最後沒有回答的有"
        f" {c.evidence_has_expected_not_answered} 題（模型自認不足或引用查核作廢）。",
        "",
    ]
    return "\n".join(lines)
