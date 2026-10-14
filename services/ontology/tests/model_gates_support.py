"""Shared builders for the model-gate tests: a scripted model and the canned annotation,
answers and cases."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from envcite_ontology.verification.extract import extract_unit
from tests.test_annotation import LAW
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED

Conn = psycopg.Connection[tuple[object, ...]]


class Scripted:
    """A model with a name, a family, and a reply — all a gate may know about it."""

    def __init__(self, name: str, reply: str | dict[str, Any] | Callable[[str], str]) -> None:
        self.name = name
        self.family = name.split(":", 1)[0]
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if callable(self.reply):
            return self.reply(prompt)
        return (
            self.reply
            if isinstance(self.reply, str)
            else json.dumps(self.reply, ensure_ascii=False)
        )


EXTRACTOR = "fake-a:extractor"


TEXT = "排放廢水者應符合標準"


SAME_AS_ANNOTATION = {
    "kind": "prescriptive",
    "deontic": "obligation",
    "conditions": [
        {
            "role": "scope",
            "code": "discharging",
            "comparator": "eq",
            "threshold": 1,
            "negated": False,
            "judgement": False,
        },
        {
            "role": "duty",
            "code": "compliant",
            "comparator": "eq",
            "threshold": 1,
            "negated": False,
            "judgement": False,
        },
    ],
    "sanction": None,
}


CASES = {
    "cases": [
        {"facts": {"discharging": 1, "compliant": 1}, "expected": "compliant"},
        {"facts": {"discharging": 1, "compliant": 0}, "expected": "violation"},
        {"facts": {"discharging": 0, "compliant": 1}, "expected": "not_applicable"},
    ]
}


FAITHFUL = {"faithful": True, "issues": []}


UNFAITHFUL = {"faithful": False, "issues": ["義務方向不對"]}


def _extracted(conn: Conn, tmp_path: Path) -> int:
    """One law, one extracted (unsettled) statement, written by `fake-a:extractor`."""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    out = extract_unit(conn, Scripted(EXTRACTOR, EXTRACTED), LAW, "art_1", agent_version="1")
    assert out.loaded is not None
    return out.loaded.ids["s1"]
