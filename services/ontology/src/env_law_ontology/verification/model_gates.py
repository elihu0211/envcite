"""要呼叫模型的閘門（env-law-docs 02-05 §5.9）：往返驗證、獨立測試、跨家族評審。

確定性閘門證明不了語意忠於原文；這三個用模型去檢查，但每一個都被設計成**不信任單一模型**：

- 驗證者不得是抽取者，也預設不得與抽取者同一家族（共同偏誤擋不住，一致不等於正確）。同家族的
  驗證者不採計；沒有任何合格的驗證者，閘門的結論是 `inconclusive`，不是 `pass`。
- 驗證者看不到它要檢查的東西的答案：往返驗證的重抽者只看標註的描述，不看原文；獨立測試的產生者
  只看原文，不看標註。它們的結論與標註不一致，才有意義。
- 模型的輸出不直接成為結論：獨立測試的案例丟進推論程式比對；評審團取多數，不採單一票。

模型閘門與確定性閘門並用，政策要求多個互相獨立的方法同時通過。沒有任何一個閘門單獨構成接受。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from env_law_ontology.reasoning import evaluate
from env_law_ontology.verification.gates import Conn, GateResult, _unit, statement_as_of
from env_law_ontology.verification.jsonutil import object_from
from env_law_ontology.verification.models import Model, family_of

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
    ("penalty", None): "罰則陳述",
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
        "SELECT d.agent FROM annotation.statement AS s"
        "  JOIN annotation.derivation AS d ON d.id = s.derivation_id WHERE s.id = %s",
        (statement_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no such statement: {statement_id}")
    return str(row[0])


def eligible(
    models: Sequence[Model], extractor: str, allow_same_family: bool
) -> tuple[list[Model], list[str]]:
    """合格的驗證者：不是抽取者；預設也不與抽取者同一家族。回傳（合格者，被略過者的名字）。"""
    home = family_of(extractor)
    ok = [m for m in models if m.name != extractor and (allow_same_family or m.family != home)]
    return ok, [m.name for m in models if m not in ok]


# ---- 把標註描述成人話（確定性，不呼叫模型） ----------------------------------------------------


def render_annotation(conn: Conn, statement_id: int) -> str:
    """標註的結構化描述：種類、行為槽、要件（帶代碼）、罰則。同一個標註永遠得到同一段文字。"""
    u = _unit(conn, statement_id)
    lines = [f"陳述種類：{_KIND.get((u.kind, u.deontic), u.kind)}"]
    for slot, value in conn.execute(
        "SELECT sl.slot::text, coalesce(k.code, sl.literal) FROM annotation.statement_slot AS sl"
        "  LEFT JOIN vocab.concept AS k ON k.id = sl.concept_id"
        " WHERE sl.statement_id = %s ORDER BY sl.slot",
        (statement_id,),
    ):
        lines.append(f"{_SLOT.get(str(slot), slot)}：{value}")
    by_role: dict[str, list[str]] = {}
    for role, code, label, cmp, threshold, unit, negated, judgement, delegated in conn.execute(
        "SELECT c.role::text, k.code, k.pref_label, c.comparator::text, c.threshold, c.unit,"
        "       c.negated, c.judgement, c.delegate_provision_id IS NOT NULL"
        "  FROM annotation.statement_condition AS c JOIN vocab.concept AS k ON k.id = c.concept_id"
        " WHERE c.statement_id = %s ORDER BY c.id",
        (statement_id,),
    ):
        if cmp == "eq" and threshold == 1 and not unit and not delegated:
            text = f"[{code}] {label} 成立"  # 成立與否的要件；不寫成原文沒有的數值
        else:
            value = (
                "門檻值由授權命令供給"
                if delegated
                else "（另一個概念）"
                if threshold is None
                else f"{threshold:g}{unit or ''}"
            )
            text = f"[{code}] {label} {_CMP[str(cmp)]} {value}"
        by_role.setdefault(str(role), []).append(
            ("非：" if negated else "") + text + ("（需判斷）" if judgement else "")
        )
    names = {"scope": "適用範圍（皆須成立）", "duty": "義務內容（皆須成立）", "limit": "限值"}
    for role in ("scope", "duty", "limit"):
        if role in by_role:
            lines.append(f"{names[role]}：" + "；".join(by_role[role]))
    row = conn.execute(
        "SELECT effect::text, min_amount, max_amount, repeating FROM annotation.sanction"
        " WHERE statement_id = %s",
        (statement_id,),
    ).fetchone()
    if row is not None:
        effect, low, high, repeating = row
        amount = (
            ""
            if low is None and high is None
            else f"；額度 {low or '無下限'} 至 {high or '無上限'}"
        )
        lines.append(f"罰則效果：{effect}{amount}" + ("；按次處罰" if repeating else ""))
    return "\n".join(lines)


def _signature(
    kind: str | None,
    deontic: str | None,
    conditions: Sequence[tuple[str, str, str, float | None, bool, bool]],
    sanction: tuple[str, float | None, float | None, bool] | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "deontic": deontic,
        "conditions": sorted(conditions, key=repr),
        "sanction": sanction,
    }


def _stored_signature(conn: Conn, statement_id: int) -> dict[str, Any]:
    u = _unit(conn, statement_id)
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
            "SELECT c.role::text, k.code, c.comparator::text, c.threshold, c.negated, c.judgement"
            "  FROM annotation.statement_condition AS c"
            "  JOIN vocab.concept AS k ON k.id = c.concept_id"
            " WHERE c.statement_id = %s",
            (statement_id,),
        )
    ]
    row = conn.execute(
        "SELECT effect::text, min_amount, max_amount, repeating FROM annotation.sanction"
        " WHERE statement_id = %s",
        (statement_id,),
    ).fetchone()
    sanction = None
    if row is not None:
        sanction = (
            str(row[0]),
            None if row[1] is None else float(cast(float, row[1])),
            None if row[2] is None else float(cast(float, row[2])),
            bool(row[3]),
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
        )
    return _signature(answer.get("kind"), answer.get("deontic"), conds, sanction)


# ---- 閘門一：往返驗證 --------------------------------------------------------------------------

REFORMALIZE = """下面是對一個法規陳述的結構化描述（不是法規原文）。
請只依這份描述，輸出一個 JSON 物件：
{{"kind": "prescriptive|constitutive|penalty",
  "deontic": "obligation|prohibition|permission 或 null",
  "conditions": [{{"role": "scope|duty|limit", "code": "方括號內的代碼",
    "comparator": "le|lt|ge|gt|eq|ne", "threshold": 數字或 null,
    "negated": true|false, "judgement": true|false}}],
  "sanction": {{"effect": "效果", "min": 數字或 null, "max": 數字或 null,
    "repeating": true|false}} 或 null}}
門檻值由授權命令供給時 threshold 為 null；「成立」表示 comparator 為 eq、threshold 為 1；
「（需判斷）」表示 judgement 為 true；「非：」表示 negated 為 true；沒有提到按次處罰則 repeating
為 false；沒有罰則則 sanction 為 null。只輸出 JSON。

描述：
{description}
"""


@dataclass(frozen=True)
class RoundtripGate:
    """標註 → 確定性的描述 → 另一個家族的模型重抽 → 比對結構。重抽者看不到原文，也看不到原標註。

    描述是確定性的（`render_annotation`），所以這一輪沒有第二個模型的誤差；重抽結果與原標註的
    種類、義務方向、要件、罰則逐項相同才通過。它檢查標註是否自我一致、可被另一個讀者還原，
    不直接檢查它與原文的距離（那是獨立測試與評審團的事）。
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


# ---- 閘門二：獨立測試 --------------------------------------------------------------------------

WRITE_TESTS = """你是法規測試設計者。下面是一段法規原文，與一組可用的概念（代碼與名稱）。
請只依原文，設計 4 至 8 個案例：每個案例給一組事實（代碼對應數值；成立為 1、不成立為 0，
數值概念給具體數值）與依原文你預期的結論。結論限於：violation（違反）、compliant（合規）、
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


@dataclass(frozen=True)
class IndependentTestGate:
    """另一個模型只讀原文、不看標註，設計案例與預期結論；案例丟進推論程式，比對結論。

    全部一致才通過；全不一致為失敗；其餘無法判定（產生者與標註各有可能錯，不武斷）。
    只對義務陳述適用：其他種類的結論不在推論程式的 verdict 裡。
    """

    tester: Model
    allow_same_family: bool = False
    method: str = "independent_test"

    @property
    def verifier(self) -> str:
        return f"gate:independent_test@1:{self.tester.name}"

    def check(self, conn: Conn, statement_id: int) -> GateResult | None:
        u = _unit(conn, statement_id)
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
        concepts = "\n".join(
            f"{code}：{label}"
            for code, label in conn.execute(
                "SELECT DISTINCT k.code, k.pref_label FROM annotation.statement_condition AS c"
                "  JOIN vocab.concept AS k ON k.id = c.concept_id"
                " WHERE c.statement_id = %s ORDER BY 1",
                (statement_id,),
            )
        )
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


# ---- 閘門三：跨家族評審團 ----------------------------------------------------------------------

JUDGE = """你是法規標註的審查者。下面是一段法規原文，與一份對它的結構化標註描述。
請判斷這份標註是否忠於原文：種類、義務方向、要件（含門檻值與「非」）、罰則的效果與額度，是否都有
原文依據，沒有遺漏或添加。描述裡的「成立」是要件成立之意，不是數值；沒有寫出的預設（例如沒有
「按次處罰」字樣）不算添加。輸出 JSON：{{"faithful": true|false, "issues": ["具體問題"]}}。
只輸出 JSON。

原文：
{text}

標註描述：
{description}
"""


@dataclass(frozen=True)
class PanelGate:
    """不同家族的模型各自判斷標註是否忠於原文，取多數。同家族與抽取者的評審不採計。

    有效票數不足兩票為無法判定；贊成與反對各自達到 `quorum` 才有結論，否則無法判定。
    評審的一致不構成接受：一致不等於正確，所以這只是政策要求的多個方法之一。
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
        u = _unit(conn, statement_id)
        prompt = JUDGE.format(text=u.quoted, description=render_annotation(conn, statement_id))
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
