"""陳述抽取的輸出格式：解碼期 JSON Schema，與補齊模型漏寫的選填欄位。"""

from typing import Any

from envcite_ontology.claims.enums import LEGAL_EFFECTS, LIABILITY_BASES, TRIGGER_KINDS
from envcite_ontology.verification.models import ConstrainedModel, Model


def _nullable(inner: dict[str, Any]) -> dict[str, Any]:
    """模型常把選填欄位照樣板填成空值：Schema 容許 null，由後面的整形與驗證處理。"""
    return {"anyOf": [inner, {"type": "null"}]}


_STR: dict[str, Any] = {"type": "string"}


_NUM: dict[str, Any] = {"type": "number"}


_BOOL: dict[str, Any] = {"type": "boolean"}


_SLOT_VALUE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "literal": _STR,
        "liability": {"enum": [*LIABILITY_BASES, None]},
        "unstated": _BOOL,
    },
}


_SLOT: dict[str, Any] = {
    "anyOf": [_SLOT_VALUE, {"type": "array", "items": _SLOT_VALUE}, _STR],
}


#: 解碼期約束只管形狀：結構、型別與列舉值。引文逐字在原文、概念已宣告、duty 與 scope 完備等
#: 語意性質不在此，仍由事後檢查負責（見 01-12 §4）。
STATEMENTS_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "statements",
        "schema": {
            "type": "object",
            "properties": {
                "concepts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"code": _STR, "label": _STR},
                        "required": ["code", "label"],
                    },
                },
                "statements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": _STR,
                            "quote": _STR,
                            "kind": {"enum": ["prescriptive", "constitutive", "sanction"]},
                            "deontic": {"enum": ["obligation", "prohibition", "permission", None]},
                            "slots": {"type": "object", "additionalProperties": _SLOT},
                            "conditions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"enum": ["scope", "duty", "limit"]},
                                        "clause": {"type": "integer"},
                                        "concept": _STR,
                                        "comparator": {
                                            "enum": ["le", "lt", "ge", "gt", "eq", "ne"]
                                        },
                                        "threshold": _nullable(_NUM),
                                        "unit": _nullable(_STR),
                                        "negated": _BOOL,
                                        "judgement": _BOOL,
                                        "value_concept": _nullable(_STR),
                                        "permit": _nullable(_STR),
                                        "method": _nullable(_STR),
                                        "averaging": _nullable(_STR),
                                    },
                                },
                            },
                            "sanction": _nullable(
                                {
                                    "type": "object",
                                    "properties": {
                                        "effect": {"enum": [*LEGAL_EFFECTS, None]},
                                        "min": _nullable(_NUM),
                                        "max": _nullable(_NUM),
                                        "amount_text": _nullable(_STR),
                                        "repeating": _BOOL,
                                        "discretionary": _BOOL,
                                        "punishes": _nullable(_STR),
                                        "triggers": _nullable(
                                            {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "statement": _STR,
                                                        "kind": {"enum": list(TRIGGER_KINDS)},
                                                    },
                                                    "required": ["statement", "kind"],
                                                },
                                            }
                                        ),
                                    },
                                }
                            ),
                        },
                        "required": ["id", "quote", "kind"],
                    },
                },
            },
            "required": ["statements"],
        },
    },
}


def complete(model: Model, prompt: str) -> str:
    """送出一次抽取呼叫；模型宣告要解碼期約束時才帶 Schema，其餘與原本相同。"""
    if isinstance(model, ConstrainedModel) and model.constrained_extraction:
        return model.complete(prompt, response_format=STATEMENTS_FORMAT)
    return model.complete(prompt)
