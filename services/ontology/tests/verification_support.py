"""Shared builders for the verification tests: record helpers, the fake extractor model, and
canned answers."""

import json
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.annotate import Loaded, load
from tests.reasoning_support import WATER, _plain_duty
from tests.test_annotation import _doc
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1

Conn = psycopg.Connection[tuple[object, ...]]


DETERMINISTIC = ["numbers", "references", "executable"]


def _review(conn: Conn, statement_id: int) -> str:
    return str(
        _scalar(
            conn, "SELECT confirm_state::text FROM annotation.statement WHERE id = %s", statement_id
        )
    )


def _latest(conn: Conn, statement_id: int, method: str) -> str:
    return str(
        _scalar(
            conn,
            "SELECT verdict::text FROM annotation.verification"
            " WHERE statement_id = %s AND method = %s ORDER BY id DESC LIMIT 1",
            statement_id,
            method,
        )
    )


def _record(conn: Conn, sid: int, method: str, verdict: str, verifier: str = "tool") -> None:
    conn.execute(
        "SELECT annotation.record_verification(%s, %s, %s, %s::annotation.verdict)",
        (sid, method, verifier, verdict),
    )
    conn.commit()


def _two_statements(conn: Conn, tmp_path: Path) -> Loaded:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", "乙一")))
    return load(conn, _doc(_plain_duty(), review="extracted"))


def _water(conn: Conn, sample: None) -> Loaded:
    return load(conn, json.loads(WATER.read_text(encoding="utf-8")))


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
