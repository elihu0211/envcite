"""要呼叫模型的閘門：往返驗證、獨立測試、跨家族評審。

確定性閘門證明不了語意忠於原文；這三個用模型檢查，但都不信任單一模型：

- 驗證者不得是抽取者，預設也不得同家族（共同偏誤擋不住）；
  無合格驗證者時結論是 `inconclusive`，不是 `pass`。
- 驗證者看不到被檢查物的答案：重抽者只看描述不看原文，測試產生者只看原文不看標註。
- 模型輸出不直接成為結論：測試案例丟進推論程式比對，評審團取多數。

任何閘門都不單獨構成接受；政策要求多個獨立方法同時通過。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.reasoning import evaluate
from envcite_ontology.verification import prompts
from envcite_ontology.verification.gates import GateResult, Verdict, statement_as_of, unit_of
from envcite_ontology.verification.jsonutil import object_from
from envcite_ontology.verification.models import Model, family_of

_CMP = {"le": "不大於", "lt": "小於", "ge": "不小於", "gt": "大於", "eq": "等於", "ne": "不等於"}
_SLOT = {
    "subject": "主體",
    "action": "行為",
    "object": "對象",
    "medium": "介質",
    "time": "時間",
    "place": "地點",
}
_KIND = {
    ("prescriptive", "obligation"): "規範性陳述・義務",
    ("prescriptive", "prohibition"): "規範性陳述・禁止",
    ("prescriptive", "permission"): "規範性陳述・許可",
    ("constitutive", None): "構成性陳述（授權、定義、限值）",
    ("sanction", None): "罰則陳述",
}
_VERDICTS = {
    "violation",
    "compliant",
    "not_applicable",
    "undetermined",
    "excluded",
    "conditional_violation",
    "conditional_compliant",
}


class _ProducerRow(NamedTuple):
    producer: str


_SQL_PRODUCER = (
    "SELECT d.producer FROM annotation.statement AS s"
    "  JOIN annotation.assertion AS sc ON sc.id = s.id"
    "  JOIN annotation.derivation AS d ON d.id = sc.derivation_id WHERE s.id = %s"
)


def _extractor(conn: Conn, statement_id: int) -> str:
    row = fetch_one(conn, _SQL_PRODUCER, (statement_id,), _ProducerRow)
    if row is None:
        raise LookupError(f"no such statement: {statement_id}")
    return row.producer


def eligible(
    models: Sequence[Model], extractor: str, allow_same_family: bool
) -> tuple[list[Model], list[str]]:
    """合格的驗證者：非抽取者，預設也非同家族。回傳（合格者，被略過者的名字）。"""
    home = family_of(extractor)
    ok = [m for m in models if m.name != extractor and (allow_same_family or m.family != home)]
    return ok, [m.name for m in models if m not in ok]


# 標註描述（確定性，不呼叫模型）

_EFFECT = {
    "fine": "罰鍰",
    "order": "命令（例如限期改善）",
    "restriction": "限制（例如停工、停業）",
    "deprivation": "剝奪（例如廢止許可、勒令歇業）",
    "reputation": "公布名稱等聲譽處分",
    "warning": "警告",
    "confiscation": "沒入",
    "disgorgement": "不法利得的追繳",
}
_LIABILITY = {"act": "行為責任", "state": "狀態責任", "representative": "代表或受指派"}
#: 主體槽位標為未明示時，描述裡寫的字。
UNSTATED = "（原文未明示）"
_TRIGGER = {"order_lapsed": "屆期未履行", "sanction_imposed": "已作成"}


class _SlotRow(NamedTuple):
    role: str
    #: 未明示的槽位是 `UNSTATED`；其餘是概念代碼或字面值（資料庫約束保證二者有一）
    value: str
    liability: str | None


class _ConditionRow(NamedTuple):
    role: str
    code: str
    label: str
    comparator: str
    threshold: Decimal | None
    unit: str | None
    negated: bool
    needs_assessment: bool
    delegated: bool
    permit: str | None
    method: str | None
    averaging: str | None
    carried: bool


class _SanctionRow(NamedTuple):
    effect: str
    low: Decimal | None
    high: Decimal | None
    per_occasion: bool
    discretionary: bool


class _TriggerRow(NamedTuple):
    effect: str
    kind: str


_SQL_SLOTS = (
    "SELECT slot_role,"
    "       CASE WHEN unstated THEN %s ELSE coalesce(concept_code, literal) END,"
    "       liability_basis"
    "  FROM annotation.slots_of(ARRAY[%s]::bigint[])"
    " ORDER BY slot_role, alternative"
)

_SQL_CONDITIONS = (
    "SELECT condition_role, concept_code, concept_name, comparator,"
    "       threshold, measure_unit, negated, needs_assessment, delegated,"
    "       permit_code, method_code, averaging_code, carried"
    "  FROM annotation.conditions_of(ARRAY[%s]::bigint[])"
    " ORDER BY id"
)

_SQL_SANCTION = (
    "SELECT legal_effect, min_amount, max_amount, per_occasion, discretionary"
    "  FROM annotation.sanctions_of(ARRAY[%s]::bigint[])"
)

_SQL_TRIGGERS = (
    "SELECT n.legal_effect::text, t.trigger_kind::text"
    "  FROM annotation.sanction_trigger AS t"
    "  JOIN annotation.sanction AS n ON n.statement_id = t.trigger_statement_id"
    " WHERE t.statement_id = %s ORDER BY n.legal_effect, t.trigger_kind"
)


def render_annotation(conn: Conn, statement_id: int, *, for_reader: bool = False) -> str:
    """標註的結構化描述（種類、行為槽、要件、罰則）；同一標註永遠得到同一段文字。

    `for_reader` 給評審團：罰則效果附中文。往返驗證的重抽者要寫回列舉值，故不附。
    """
    u = unit_of(conn, statement_id)
    lines = [f"陳述種類：{_KIND.get((u.kind, u.deontic), u.kind)}"]
    for slot in fetch(conn, _SQL_SLOTS, (UNSTATED, statement_id), _SlotRow):
        basis = (
            f"（責任基礎：{_LIABILITY.get(slot.liability, slot.liability)}）"
            if slot.liability
            else ""
        )
        lines.append(f"{_SLOT.get(slot.role, slot.role)}：{slot.value}{basis}")
    by_role: dict[str, list[str]] = {}
    for cond in fetch(conn, _SQL_CONDITIONS, (statement_id,), _ConditionRow):
        if cond.comparator == "eq" and cond.threshold == 1 and not cond.unit and not cond.delegated:
            text = f"[{cond.code}] {cond.label} 成立"  # 不寫成原文沒有的數值
        else:
            value = (
                "門檻值由授權命令供給"
                if cond.delegated
                else f"門檻值由個別許可（{cond.permit}）所載"
                if cond.permit is not None
                else "（另一個概念）"
                if cond.threshold is None
                else f"{cond.threshold:g}{cond.unit or ''}"
            )
            text = f"[{cond.code}] {cond.label} {_CMP[cond.comparator]} {value}"
        if cond.method is not None or cond.averaging is not None:
            text += (
                f"（檢測方法：{cond.method or '未標註'}；平均時段：{cond.averaging or '未標註'}）"
            )
        by_role.setdefault(cond.role, []).append(
            ("非：" if cond.negated else "")
            + text
            + ("（需判斷）" if cond.needs_assessment else "")
            + ("（承自上位）" if cond.carried else "")
        )
    names = {"scope": "適用範圍（皆須成立）", "duty": "義務內容（皆須成立）", "limit": "限值"}
    for role in ("scope", "duty", "limit"):
        if role in by_role:
            lines.append(f"{names[role]}：" + "；".join(by_role[role]))
    sanction = fetch_one(conn, _SQL_SANCTION, (statement_id,), _SanctionRow)
    if sanction is not None:
        amount = (
            ""
            if sanction.low is None and sanction.high is None
            else f"；額度 {sanction.low or '無下限'} 至 {sanction.high or '無上限'}"
        )
        shown = (
            f"{sanction.effect}（{_EFFECT.get(sanction.effect, sanction.effect)}）"
            if for_reader
            else sanction.effect
        )
        lines.append(
            f"罰則效果：{shown}{amount}"
            + ("；按次處罰" if sanction.per_occasion else "")
            + ("；得（裁量）" if sanction.discretionary else "")
        )
        for trigger in fetch(conn, _SQL_TRIGGERS, (statement_id,), _TriggerRow):
            shown = (
                f"{trigger.effect}（{_EFFECT.get(trigger.effect, trigger.effect)}）"
                if for_reader
                else trigger.effect
            )
            lines.append(f"前提：另一罰則 {shown} {_TRIGGER.get(trigger.kind, trigger.kind)}")
    return "\n".join(lines)


def _signature(
    kind: str | None,
    deontic: str | None,
    conditions: Sequence[tuple[str, str, str, float | None, bool, bool]],
    sanction: tuple[str, float | None, float | None, bool, bool] | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "deontic": deontic,
        "conditions": sorted(conditions, key=repr),
        "sanction": sanction,
    }


class _StoredConditionRow(NamedTuple):
    role: str
    code: str
    comparator: str
    threshold: Decimal | None
    negated: bool
    needs_assessment: bool


_SQL_STORED_CONDITIONS = (
    "SELECT c.condition_role::text, k.code, c.comparator::text, c.threshold,"
    "       c.negated, c.needs_assessment"
    "  FROM annotation.statement_condition AS c"
    "  JOIN vocab.concept AS k ON k.id = c.concept_id"
    " WHERE c.statement_id = %s"
)


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _stored_signature(conn: Conn, statement_id: int) -> dict[str, Any]:
    u = unit_of(conn, statement_id)
    conds = [
        (c.role, c.code, c.comparator, _float(c.threshold), c.negated, c.needs_assessment)
        for c in fetch(conn, _SQL_STORED_CONDITIONS, (statement_id,), _StoredConditionRow)
    ]
    row = fetch_one(conn, _SQL_SANCTION, (statement_id,), _SanctionRow)
    sanction = None
    if row is not None:
        sanction = (
            row.effect,
            _float(row.low),
            _float(row.high),
            row.per_occasion,
            row.discretionary,
        )
    return _signature(u.kind, u.deontic, conds, sanction)


def _answered_signature(answer: Mapping[str, Any]) -> dict[str, Any]:
    conds: list[tuple[str, str, str, float | None, bool, bool]] = []
    for c in answer.get("conditions") or []:
        threshold = c.get("threshold")
        conds.append(
            (
                str(c.get("role")),
                str(c.get("code")),
                str(c.get("comparator")),
                None if threshold is None else float(threshold),
                bool(c.get("negated", False)),
                bool(c.get("judgement", False)),
            )
        )
    s = answer.get("sanction")
    sanction = None
    if isinstance(s, Mapping):
        low, high = s.get("min"), s.get("max")
        sanction = (
            str(s.get("effect")),
            None if low is None else float(low),
            None if high is None else float(high),
            bool(s.get("repeating", False)),
            bool(s.get("discretionary", False)),
        )
    # 只有規範性陳述有義務方向（同資料庫限制與載入處理）。重抽者在授權、定義上寫 permission 是把
    # 「得」讀成許可，不是另一種結構；不在此丟掉，整類授權條款會因不對稱被擋下。
    kind = answer.get("kind")
    deontic = answer.get("deontic") if kind == "prescriptive" else None
    return _signature(kind, deontic, conds, sanction)


# 閘門一：往返驗證


@dataclass(frozen=True)
class RoundtripGate:
    """標註 → 確定性描述 → 另一家族模型重抽 → 比對結構；重抽者看不到原文與原標註。

    種類、義務方向、要件、罰則逐項相同才通過。檢查的是標註可被還原，不是它與原文的距離
    （那是獨立測試與評審團的事）。
    """

    reformalizer: Model
    allow_same_family: bool = False
    method: str = "roundtrip"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含重抽者的名稱。"""
        return f"gate:roundtrip@1:{self.reformalizer.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """重抽者只看標註描述寫回的結構與標註逐項相同為 pass，有差異為 fail。

        沒有合格的重抽者或答案取不出 JSON 為 inconclusive；差異列在證據的 `differences`。
        """
        ok, skipped = eligible(
            [self.reformalizer], _extractor(conn, statement_id), self.allow_same_family
        )
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        answer = object_from(
            ok[0].complete(prompts.reformalize(description=render_annotation(conn, statement_id)))
        )
        if answer is None:
            return GateResult("inconclusive", {"reason": "unparseable answer"})
        mine, theirs = _stored_signature(conn, statement_id), _answered_signature(answer)
        if mine == theirs:
            return GateResult("pass", {"verifier": ok[0].name})
        diff = {
            k: {"annotation": mine[k], "roundtrip": theirs[k]} for k in mine if mine[k] != theirs[k]
        }
        return GateResult("fail", {"verifier": ok[0].name, "differences": diff})


# 閘門二：獨立測試


class _ConceptRow(NamedTuple):
    code: str
    label: str
    comparator: str
    threshold: Decimal | None
    unit: str | None


_SQL_CONCEPTS = (
    "SELECT k.code, k.name, c.comparator::text, c.threshold, c.measure_unit"
    "  FROM annotation.statement_condition AS c"
    "  JOIN vocab.concept AS k ON k.id = c.concept_id"
    " WHERE c.statement_id = %s ORDER BY k.code, c.id"
)


def _concept_line(conn: Conn, statement_id: int) -> list[str]:
    """給測試設計者的概念清單，附型別（取自標註的要件；無單位的 eq 1 是成立與否）。

    不說明型別，設計者會對成立與否的概念編出數值，那是型別歧義，不是標註錯。
    """
    lines: dict[str, tuple[str, set[str]]] = {}
    for concept in fetch(conn, _SQL_CONCEPTS, (statement_id,), _ConceptRow):
        presence = concept.comparator == "eq" and concept.threshold == 1 and not concept.unit
        kind = lines.setdefault(concept.code, (concept.label, set()))[1]
        kind.add(
            "成立與否，成立為 1、不成立為 0"
            if presence
            else f"數值{f'，單位 {concept.unit}' if concept.unit else ''}"
        )
    return [
        f"{code}：{label}（{'；'.join(sorted(kinds))}）" for code, (label, kinds) in lines.items()
    ]


@dataclass(frozen=True)
class IndependentTestGate:
    """另一個模型只讀原文設計案例與預期結論，案例丟進推論程式比對。

    全部一致才通過、全不一致為失敗，其餘無法判定（產生者與標註都可能錯）。
    只適用義務陳述：其他種類的結論不在推論程式的 verdict 裡。
    """

    tester: Model
    allow_same_family: bool = False
    method: str = "independent_test"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含測試設計者的名稱。"""
        return f"gate:independent_test@1:{self.tester.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """設計者的案例與推論程式的結論全一致為 pass、全不一致為 fail，其餘為 inconclusive。

        不是義務陳述回 None。沒有合格的設計者、沒有生效起日，或可用、可執行的案例各不足三個，
        都是 inconclusive。
        """
        u = unit_of(conn, statement_id)
        if not (u.kind == "prescriptive" and u.deontic == "obligation"):
            return None
        ok, skipped = eligible(
            [self.tester], _extractor(conn, statement_id), self.allow_same_family
        )
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        as_of = statement_as_of(conn, statement_id)
        if as_of is None:
            return GateResult("inconclusive", {"reason": "no start date for the text"})
        concepts = _concept_line(conn, statement_id)
        answer = object_from(ok[0].complete(prompts.write_tests(text=u.quoted, concepts=concepts)))
        cases = [
            c
            for c in (answer or {}).get("cases") or []
            if isinstance(c, Mapping) and c.get("expected") in _VERDICTS
        ]
        if len(cases) < 3:
            return GateResult("inconclusive", {"reason": "fewer than three usable cases"})
        results: list[dict[str, Any]] = []
        for c in cases:
            try:
                facts = {str(k): float(v) for k, v in dict(c.get("facts") or {}).items()}
            except TypeError, ValueError:
                continue
            got = evaluate(conn, as_of, facts, include_unsettled=True).verdicts.get(statement_id)
            results.append(
                {
                    "facts": facts,
                    "expected": c["expected"],
                    "got": got,
                    "agree": got == c["expected"],
                }
            )
        if len(results) < 3:
            return GateResult("inconclusive", {"reason": "fewer than three runnable cases"})
        agree = sum(r["agree"] for r in results)
        verdict: Verdict = (
            "pass" if agree == len(results) else ("fail" if agree == 0 else "inconclusive")
        )
        return GateResult(verdict, {"verifier": ok[0].name, "agree": agree, "cases": results})


# 閘門三：跨家族評審團


@dataclass(frozen=True)
class PanelGate:
    """不同家族的模型各自判斷標註是否忠於原文，取多數；與抽取者同家族的評審不採計。

    有效票不足兩票、或贊成與反對都未達 `quorum`，為無法判定。評審一致不等於正確，只是多個方法之一。
    """

    judges: tuple[Model, ...]
    quorum: float = 2 / 3
    allow_same_family: bool = False
    min_families: int = 2
    method: str = "panel"

    @property
    def verifier(self) -> str:
        """記入紀錄的驗證者識別，含全體評審的名稱。"""
        return "gate:panel@1:" + "+".join(j.name for j in self.judges)

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        """贊成票比率達 `quorum` 為 pass，反對票比率達 `quorum` 為 fail，其餘為 inconclusive。

        合格評審的家族少於 `min_families`，或有效票少於兩票，也是 inconclusive。任一評審丟
        `ModelError` 時不在此接住，整個閘門交由呼叫端記為 inconclusive。
        """
        ok, skipped = eligible(self.judges, _extractor(conn, statement_id), self.allow_same_family)
        if len({m.family for m in ok}) < self.min_families:
            return GateResult(
                "inconclusive",
                {"reason": "fewer than the required model families", "skipped": skipped},
            )
        u = unit_of(conn, statement_id)
        prompt = prompts.judge(
            context=u.context,
            text=u.text,
            quote=u.quoted,
            description=render_annotation(conn, statement_id, for_reader=True),
        )
        votes: list[dict[str, Any]] = []
        for judge in ok:
            answer = object_from(judge.complete(prompt))
            if answer is not None and isinstance(answer.get("faithful"), bool):
                votes.append(
                    {
                        "judge": judge.name,
                        "faithful": answer["faithful"],
                        "issues": answer.get("issues", []),
                    }
                )
        if len(votes) < 2:
            return GateResult(
                "inconclusive", {"reason": "fewer than two valid votes", "votes": votes}
            )
        yes = sum(v["faithful"] for v in votes)
        share = yes / len(votes)
        evidence = {"votes": votes, "skipped": skipped}
        if share >= self.quorum:
            return GateResult("pass", evidence)
        if (1 - share) >= self.quorum:
            return GateResult("fail", evidence)
        return GateResult("inconclusive", evidence)
