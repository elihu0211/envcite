"""陳述的形狀：整形（`tidy`）與概念、角色與條件的結構檢查。"""

from collections.abc import Mapping, Sequence
from typing import Any

from envcite_ontology.claims.enums import LIABILITY_BASES


def statements_of(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """模型回答裡的陳述；形狀不對的項目當作沒有（外來輸出不可信）。"""
    items = answer.get("statements")
    return [x for x in items if isinstance(x, Mapping)] if isinstance(items, list) else []


def concepts_of(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """宣告完整（有 code 與 label）的概念；別的項目當作沒宣告。

    只留提示要的欄位：模型自己多寫的鍵（例如 broader）不可信，載入器會因它拒絕整份文件。
    """
    items = answer.get("concepts")
    return [
        {"code": c["code"], "label": c["label"]}
        for c in (items if isinstance(items, list) else [])
        if isinstance(c, Mapping) and c.get("code") and c.get("label")
    ]


def _literal(value: object) -> object:
    """槽位值裡的字串是字面；清單逐項處理。"""
    if isinstance(value, str):
        return {"literal": value}
    if isinstance(value, list):
        return [_literal(v) for v in value]
    return value


def _liability(role: str, value: object) -> object:
    """責任基礎只屬主體、只取列舉值；別處或別的值當作沒寫（載入器會因它拒絕整份文件）。

    「未明示」同樣只屬主體；別的槽位寫它等於沒有值，整個槽位值去掉。
    """
    if isinstance(value, list):
        kept = [_liability(role, v) for v in value]
        return [v for v in kept if v != {}]
    if isinstance(value, Mapping):
        if "liability" in value and (
            role != "subject" or value["liability"] not in LIABILITY_BASES
        ):
            value = {k: v for k, v in value.items() if k != "liability"}
        if role != "subject" and "unstated" in value:
            value = {k: v for k, v in value.items() if k != "unstated"}
    return value


def _tidy_condition(c: Mapping[str, Any]) -> dict[str, Any]:
    """一個要件的形狀修正（見 `tidy`）。承自上位是程式標的（`carry`），模型寫的不算。"""
    drop: set[str] = {"carried"}
    if c.get("value_concept") or c.get("permit"):
        drop.add("threshold")
    if c.get("role") == "limit":
        drop.add("judgement")
    else:
        drop |= {"method", "averaging"}
    return {k: v for k, v in c.items() if k not in drop}


def tidy(answer: Mapping[str, Any], defined: Mapping[str, str] | None = None) -> dict[str, Any]:
    """不改變意思的形狀修正。

    槽位直接寫成字串（`"subject": "甲"`，或清單裡的字串）就是它的字面；與概念值比較或委由
    個別處分的要件沒有門檻值，模型多寫的 threshold 去掉；主體以外或不在列舉裡的責任基礎去掉；
    認定方式只屬限值、限值不標需判斷，別處的去掉（載入器會因它拒絕整份文件）；
    要件指名的本法用語代碼，補進宣告的概念（名稱取自該法的定義）。觸發不在這裡修：少了前提的
    罰則會變成無條件，由 `_local_triggers` 整個丟掉。
    """
    out = dict(answer)
    out["statements"] = [
        {
            **s,
            "slots": {k: _liability(str(k), _literal(v)) for k, v in slots.items()},
        }
        if isinstance(slots := s.get("slots"), Mapping)
        else dict(s)
        for s in statements_of(answer)
    ]
    for s in out["statements"]:
        conditions = s.get("conditions")
        if not isinstance(conditions, list):
            continue
        s["conditions"] = [_tidy_condition(c) if isinstance(c, Mapping) else c for c in conditions]
    if defined:
        declared = {str(c["code"]) for c in concepts_of(answer)}
        used = {
            str(c["value_concept"])
            for s in out["statements"]
            for c in s.get("conditions") or []
            if isinstance(c, Mapping) and str(c.get("value_concept")) in defined
        }
        out["concepts"] = [
            *concepts_of(answer),
            *({"code": code, "label": defined[code]} for code in sorted(used - declared)),
        ]
    return out


def shape_issues(
    answer: Mapping[str, Any], defined: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """載入器必然拒絕的形狀（id → 問題）：要件非物件、無 concept、concept 未宣告、
    概念值要件比大小，或罰則陳述的種類不是 sanction。

    載入器一遇到就整份不寫、連帶丟掉正確的陳述；故先請模型修，修不好才只丟該陳述。
    """
    declared = {str(c["code"]) for c in concepts_of(answer)} | set(defined or {})
    out: dict[str, list[str]] = {}
    for s in statements_of(answer):
        problems: list[str] = []
        for c in s.get("conditions") or []:
            if not isinstance(c, Mapping):
                problems.append("要件必須是物件（role、concept、comparator、threshold…）。")
                continue
            for field in ("concept", "value_concept", "permit", "method", "averaging"):
                code = c.get(field)
                if field == "concept" and not code:
                    problems.append(
                        f"有一個要件沒有 concept（role={c.get('role')}）：每個要件都要指名一個"
                        "在 concepts 裡宣告過的代碼。"
                    )
                elif field == "value_concept" and code and str(code) not in (defined or {}):
                    problems.append(
                        f"要件的 value_concept「{code}」不在本法已定義的用語清單裡；"
                        "清單沒有的不要寫 value_concept。"
                    )
                elif code and str(code) not in declared:
                    problems.append(
                        f"要件的 {field}「{code}」沒有在 concepts 裡宣告（code 與 label 都要寫）。"
                    )
            if c.get("role") == "limit" and c.get("threshold") is None:
                problems.append(f"限值「{c.get('concept')}」沒有 threshold：限值要寫原文的數值。")
            if c.get("value_concept") and c.get("permit"):
                problems.append(
                    f"要件「{c.get('concept')}」同時寫了 value_concept 與 permit："
                    "門檻值只能有一種來源。"
                )
            if c.get("value_concept") and c.get("comparator") not in ("eq", "ne"):
                problems.append(
                    f"要件的 value_concept「{c.get('value_concept')}」只能用 eq 或 ne："
                    "用語只有屬於與否，沒有大小。"
                )
        # 只看填了效果的罰則：照樣板填成空值的會被忽略；處罰別的罰則者留待連結，不在這裡判
        sanction = s.get("sanction")
        if isinstance(sanction, Mapping) and sanction.get("effect") and s.get("kind") != "sanction":
            problems.append("帶 sanction 的陳述 kind 必須是 sanction。")
        if problems:
            out[str(s.get("id", "?"))] = problems
    return out


def borrowed_issues(
    statements: Sequence[Mapping[str, Any]], defined: Mapping[str, str] | None
) -> list[str]:
    """義務內容或適用範圍借用本法已定義的泛稱（約定 G）：只請模型修，修不好不丟，交給驗證。

    用語只屬 value_concept；以它當 duty 的概念，標註說的不是引文的那件事。
    """
    if not defined:
        return []
    return [
        f"陳述 {s.get('id', '?')} 的 {c.get('role')} 要件借用本法已定義的用語「{c['concept']}」"
        "當概念：請依約定 G 另外宣告，用引文自己的字命名這句話說的那件事"
        "（用語只能放 value_concept）。"
        for s in statements
        if s.get("kind") == "prescriptive"
        for c in s.get("conditions") or []
        if isinstance(c, Mapping)
        and c.get("role") in ("duty", "scope")
        and str(c.get("concept")) in defined
        and not c.get("value_concept")
    ]
