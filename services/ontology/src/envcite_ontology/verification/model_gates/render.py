"""標註 → 確定性描述：給重抽者與評審團看的文字，不含原文。"""

from decimal import Decimal
from typing import NamedTuple

from envcite_ontology.db import Conn, fetch, fetch_one
from envcite_ontology.verification.gates import unit_of

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


class SanctionRow(NamedTuple):
    """陳述的罰則列：效果、金額區間、按次與裁量。"""

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


SQL_SANCTION = (
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
    sanction = fetch_one(conn, SQL_SANCTION, (statement_id,), SanctionRow)
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
