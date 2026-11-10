"""要呼叫模型的閘門：往返驗證、獨立測試、跨家族評審。

確定性閘門證明不了語意忠於原文；這三個用模型檢查，但都不信任單一模型：

- 驗證者不得是抽取者，預設也不得同家族（共同偏誤擋不住）；
  無合格驗證者時結論是 `inconclusive`，不是 `pass`。
- 驗證者看不到被檢查物的答案：重抽者只看描述不看原文，測試產生者只看原文不看標註。
- 模型輸出不直接成為結論：測試案例丟進推論程式比對，評審團取多數。

任何閘門都不單獨構成接受；政策要求多個獨立方法同時通過。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from envcite_ontology.reasoning import evaluate
from envcite_ontology.verification.context import context_block
from envcite_ontology.verification.conventions import apply as with_conventions
from envcite_ontology.verification.gates import Conn, GateResult, statement_as_of, unit_of
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


def _extractor(conn: Conn, statement_id: int) -> str:
    row = conn.execute(
        "SELECT d.producer FROM annotation.statement AS s"
        "  JOIN annotation.assertion AS sc ON sc.id = s.id"
        "  JOIN annotation.derivation AS d ON d.id = sc.derivation_id WHERE s.id = %s",
        (statement_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no such statement: {statement_id}")
    return str(row[0])


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


def render_annotation(conn: Conn, statement_id: int, *, for_reader: bool = False) -> str:
    """標註的結構化描述（種類、行為槽、要件、罰則）；同一標註永遠得到同一段文字。

    `for_reader` 給評審團：罰則效果附中文。往返驗證的重抽者要寫回列舉值，故不附。
    """
    u = unit_of(conn, statement_id)
    lines = [f"陳述種類：{_KIND.get((u.kind, u.deontic), u.kind)}"]
    for slot, value, liability in conn.execute(
        "SELECT slot_role,"
        "       CASE WHEN unstated THEN %s ELSE coalesce(concept_code, literal) END,"
        "       liability_basis"
        "  FROM annotation.slots_of(ARRAY[%s]::bigint[])"
        " ORDER BY slot_role, alternative",
        (UNSTATED, statement_id),
    ):
        basis = f"（責任基礎：{_LIABILITY.get(str(liability), liability)}）" if liability else ""
        lines.append(f"{_SLOT.get(str(slot), slot)}：{value}{basis}")
    by_role: dict[str, list[str]] = {}
    for found in conn.execute(
        "SELECT condition_role, concept_code, concept_name, comparator,"
        "       threshold, measure_unit, negated, needs_assessment, delegated,"
        "       permit_code, method_code, averaging_code, carried"
        "  FROM annotation.conditions_of(ARRAY[%s]::bigint[])"
        " ORDER BY id",
        (statement_id,),
    ):
        role, code, label, cmp, threshold, unit, negated, judgement, delegated = found[:9]
        permit, method, averaging, carried = found[9:]
        if cmp == "eq" and threshold == 1 and not unit and not delegated:
            text = f"[{code}] {label} 成立"  # 不寫成原文沒有的數值
        else:
            value = (
                "門檻值由授權命令供給"
                if delegated
                else f"門檻值由個別許可（{permit}）所載"
                if permit is not None
                else "（另一個概念）"
                if threshold is None
                else f"{threshold:g}{unit or ''}"
            )
            text = f"[{code}] {label} {_CMP[str(cmp)]} {value}"
        if method is not None or averaging is not None:
            text += f"（檢測方法：{method or '未標註'}；平均時段：{averaging or '未標註'}）"
        by_role.setdefault(str(role), []).append(
            ("非：" if negated else "")
            + text
            + ("（需判斷）" if judgement else "")
            + ("（承自上位）" if carried else "")
        )
    names = {"scope": "適用範圍（皆須成立）", "duty": "義務內容（皆須成立）", "limit": "限值"}
    for role in ("scope", "duty", "limit"):
        if role in by_role:
            lines.append(f"{names[role]}：" + "；".join(by_role[role]))
    row = conn.execute(
        "SELECT legal_effect, min_amount, max_amount, per_occasion, discretionary"
        "  FROM annotation.sanctions_of(ARRAY[%s]::bigint[])",
        (statement_id,),
    ).fetchone()
    if row is not None:
        effect, low, high, repeating, discretionary = row
        amount = (
            ""
            if low is None and high is None
            else f"；額度 {low or '無下限'} 至 {high or '無上限'}"
        )
        shown = f"{effect}（{_EFFECT.get(str(effect), effect)}）" if for_reader else effect
        lines.append(
            f"罰則效果：{shown}{amount}"
            + ("；按次處罰" if repeating else "")
            + ("；得（裁量）" if discretionary else "")
        )
        for effect, kind in conn.execute(
            "SELECT n.legal_effect::text, t.trigger_kind::text"
            "  FROM annotation.sanction_trigger AS t"
            "  JOIN annotation.sanction AS n ON n.statement_id = t.trigger_statement_id"
            " WHERE t.statement_id = %s ORDER BY n.legal_effect, t.trigger_kind",
            (statement_id,),
        ):
            shown = f"{effect}（{_EFFECT.get(str(effect), effect)}）" if for_reader else effect
            lines.append(f"前提：另一罰則 {shown} {_TRIGGER.get(str(kind), kind)}")
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


def _stored_signature(conn: Conn, statement_id: int) -> dict[str, Any]:
    u = unit_of(conn, statement_id)
    conds = [
        (
            str(r[0]),
            str(r[1]),
            str(r[2]),
            None if r[3] is None else float(cast(float, r[3])),
            bool(r[4]),
            bool(r[5]),
        )
        for r in conn.execute(
            "SELECT c.condition_role::text, k.code, c.comparator::text, c.threshold,"
            "       c.negated, c.needs_assessment"
            "  FROM annotation.statement_condition AS c"
            "  JOIN vocab.concept AS k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s",
            (statement_id,),
        )
    ]
    row = conn.execute(
        "SELECT legal_effect, min_amount, max_amount, per_occasion, discretionary"
        "  FROM annotation.sanctions_of(ARRAY[%s]::bigint[])",
        (statement_id,),
    ).fetchone()
    sanction = None
    if row is not None:
        sanction = (
            str(row[0]),
            None if row[1] is None else float(cast(float, row[1])),
            None if row[2] is None else float(cast(float, row[2])),
            bool(row[3]),
            bool(row[4]),
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

REFORMALIZE = with_conventions("""下面是對一個法規陳述的結構化描述（不是法規原文）。
請只依這份描述，輸出一個 JSON 物件：
{{"kind": "prescriptive|constitutive|sanction",
  "deontic": "obligation|prohibition|permission 或 null",
  "conditions": [{{"role": "scope|duty|limit", "code": "方括號內的代碼",
    "comparator": "le|lt|ge|gt|eq|ne", "threshold": 數字或 null,
    "negated": true|false, "judgement": true|false}}],
  "sanction": {{"effect": "效果", "min": 數字或 null, "max": 數字或 null,
    "repeating": true|false, "discretionary": true|false}} 或 null}}
門檻值由授權命令供給時 threshold 為 null；「成立」表示 comparator 為 eq、threshold 為 1；
「（需判斷）」表示 judgement 為 true；「非：」表示 negated 為 true；沒有提到按次處罰則 repeating
為 false；「得（裁量）」表示 discretionary 為 true，沒有提到則為 false；
沒有罰則則 sanction 為 null。只輸出 JSON。

{conventions}
描述：
{description}
""")


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
        return f"gate:roundtrip@1:{self.reformalizer.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        ok, skipped = eligible(
            [self.reformalizer], _extractor(conn, statement_id), self.allow_same_family
        )
        if not ok:
            return GateResult(
                "inconclusive", {"reason": "no eligible verifier", "skipped": skipped}
            )
        answer = object_from(
            ok[0].complete(REFORMALIZE.format(description=render_annotation(conn, statement_id)))
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

WRITE_TESTS = """你是法規測試設計者。下面是一段法規原文，與一組可用的概念（代碼與名稱）。
請只依原文，設計 4 至 8 個案例：每個案例給一組事實（代碼對應數值；成立與否的概念給 1 或 0，
數值概念給具體數值，型別見概念清單）與依原文你預期的結論。結論限於：violation（違反）、compliant（合規）、
not_applicable（本條不適用）、undetermined（事實不足以判定）。涵蓋合規、違反、適用範圍不成立各
至少一個。輸出 JSON：
{{"cases": [{{"facts": {{"代碼": 數值}},
  "expected": "violation|compliant|not_applicable|undetermined"}}]}}
只輸出 JSON。

原文：
{text}

可用的概念：
{concepts}
"""


def _concept_line(conn: Conn, statement_id: int) -> list[str]:
    """給測試設計者的概念清單，附型別（取自標註的要件；無單位的 eq 1 是成立與否）。

    不說明型別，設計者會對成立與否的概念編出數值，那是型別歧義，不是標註錯。
    """
    rows = conn.execute(
        "SELECT k.code, k.name, c.comparator::text, c.threshold, c.measure_unit"
        "  FROM annotation.statement_condition AS c"
        "  JOIN vocab.concept AS k ON k.id = c.concept_id"
        " WHERE c.statement_id = %s ORDER BY k.code, c.id",
        (statement_id,),
    )
    lines: dict[str, tuple[str, set[str]]] = {}
    for code, label, cmp, threshold, unit in rows:
        presence = cmp == "eq" and threshold == 1 and not unit
        kind = lines.setdefault(str(code), (str(label), set()))[1]
        kind.add(
            "成立與否，成立為 1、不成立為 0"
            if presence
            else f"數值{f'，單位 {unit}' if unit else ''}"
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
        return f"gate:independent_test@1:{self.tester.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
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
        concepts = "\n".join(_concept_line(conn, statement_id))
        answer = object_from(ok[0].complete(WRITE_TESTS.format(text=u.quoted, concepts=concepts)))
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
            except (TypeError, ValueError):
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
        verdict = "pass" if agree == len(results) else ("fail" if agree == 0 else "inconclusive")
        return GateResult(
            cast(Any, verdict), {"verifier": ok[0].name, "agree": agree, "cases": results}
        )


# 閘門三：跨家族評審團

JUDGE = with_conventions("""你是法規標註的審查者。
下面是一個條文單元的原文，與一份對其中「標註對應的引文」的結構化
標註描述。一個單元常被拆成幾個陳述，每個陳述只對應其中一段引文，其餘條件在別的陳述裡；請就這一段
引文判斷，需要前後文時看整個單元，不要要求這個陳述涵蓋引文以外的內容。
請判斷這份標註是否忠於引文（與它所在的單元）：種類、義務方向、要件（含門檻值與「非」）、罰則的效果與額度，是否都有
原文依據，沒有遺漏或添加。描述裡的「成立」是要件成立之意，不是數值；沒有寫出的預設（例如沒有
「按次處罰」字樣）不算添加。主體為「（原文未明示）」是說原文沒有寫出義務人，不算遺漏；主體若是
被規範的物或行為而不是人，才是錯。標有「承自上位」的適用範圍要件，是程式從上層條文或句首帶入的，
不算添加。輸出 JSON：{{"faithful": true|false, "issues": ["具體問題"]}}。
只輸出 JSON。

{conventions}
{context}整個單元的原文：
{text}

標註對應的引文：
{quote}

標註描述：
{description}
""")


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
        return "gate:panel@1:" + "+".join(j.name for j in self.judges)

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        ok, skipped = eligible(self.judges, _extractor(conn, statement_id), self.allow_same_family)
        if len({m.family for m in ok}) < self.min_families:
            return GateResult(
                "inconclusive",
                {"reason": "fewer than the required model families", "skipped": skipped},
            )
        u = unit_of(conn, statement_id)
        prompt = JUDGE.format(
            context=context_block(u.context),
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
