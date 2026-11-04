"""Shared builders for the verification tests: record helpers, the fake extractor model, and
canned answers."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.annotate import Loaded, load
from envcite_ontology.verification.link import link_penalties
from tests.reasoning_support import WATER, _plain_duty
from tests.test_annotation import _doc
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]


DETERMINISTIC = ["numbers", "references", "executable"]


def _review(conn: Conn, statement_id: int) -> str:
    return str(
        _scalar(
            conn, "SELECT confirm_state::text FROM annotation.assertion WHERE id = %s", statement_id
        )
    )


def _latest(conn: Conn, statement_id: int, method: str) -> str:
    return str(
        _scalar(
            conn,
            "SELECT result::text FROM annotation.verification"
            " WHERE assertion_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
            statement_id,
            method,
        )
    )


def _record(conn: Conn, sid: int, method: str, verdict: str, verifier: str = "tool") -> None:
    conn.execute(
        "SELECT annotation.record_verification(%s, %s, %s, %s::annotation.gate_result)",
        (sid, method, verifier, verdict),
    )
    conn.commit()


def _two_statements(conn: Conn, tmp_path: Path) -> Loaded:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    return load(conn, _doc(_plain_duty(), review="extracted"))


def _with_penalties(conn: Conn, doc: dict[str, Any]) -> Loaded:
    """載入草稿、確認罰則所處罰的義務（審核者的角色），再把待連結罰則寫成陳述。

    綱要只讓罰則處罰已確認的陳述，所以罰則的閘門與推論要在義務確認之後才看得到罰則。
    回傳的 id 沿用文件裡的名字（一個罰則只處罰一個義務時，去掉連結加上的「@編號」）。
    """
    loaded = load(conn, doc)
    conn.execute(
        "UPDATE annotation.assertion SET confirm_state = 'confirmed'"
        " WHERE id IN (SELECT punished_statement_id FROM annotation.held_penalty)"
    )
    conn.commit()
    linked = link_penalties(conn, None)
    assert not linked.dropped, linked.dropped
    named = {k.split("@")[0]: v for k, v in linked.ids.items()}
    return replace(
        loaded,
        statements=loaded.statements + linked.statements,
        sanctions=len(named),
        ids={**loaded.ids, **named},
    )


def _water_draft(conn: Conn, sample: None) -> Loaded:
    """水污法樣本照原樣載入：義務未確認，十個罰則存成待連結罰則。"""
    return load(conn, json.loads(WATER.read_text(encoding="utf-8")))


def _water(conn: Conn, sample: None) -> Loaded:
    return _with_penalties(conn, json.loads(WATER.read_text(encoding="utf-8")))


def _water_with(conn: Conn, sid: str, **sanction: Any) -> Loaded:
    """水污法樣本，其中一個罰則改成模型寫錯的樣子再載入：主張封存後內容不能改，錯要錯在載入前。"""
    doc = json.loads(WATER.read_text(encoding="utf-8"))
    s = next(x for x in doc["statements"] if x["id"] == sid)
    s["sanction"] = {**s["sanction"], **sanction}
    return _with_penalties(conn, doc)


class FakeModel:
    name = "fake:model"
    family = "fake"

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return (
            self.answer
            if isinstance(self.answer, str)
            else json.dumps(self.answer, ensure_ascii=False)
        )


EXTRACTED = {
    "concepts": [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "compliant", "label": "符合標準"},
    ],
    "statements": [
        {
            "id": "s1",
            "quote": "排放廢水者應符合標準",
            "kind": "prescriptive",
            "deontic": "obligation",
            "conditions": [
                {
                    "role": "scope",
                    "clause": 1,
                    "concept": "discharging",
                    "comparator": "eq",
                    "threshold": 1,
                },
                {
                    "role": "duty",
                    "clause": 1,
                    "concept": "compliant",
                    "comparator": "eq",
                    "threshold": 1,
                },
            ],
        },
        {"id": "invented", "quote": "原文沒有這句話", "kind": "constitutive"},
    ],
}


def _duty_and_cited_penalty() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(json.dumps(EXTRACTED))
    doc["statements"] = [
        doc["statements"][0],
        {
            "id": "p1",
            "quote": "排放廢水者應符合標準",
            "kind": "sanction",
            "sanction": {"effect": "fine", "min": 1, "max": 2},
        },
    ]
    return doc


class Sequence_:
    """A model that gives its answers in turn (the last one repeats)."""

    name = "fake:sequence"
    family = "fake"

    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        return json.dumps(answer, ensure_ascii=False)
