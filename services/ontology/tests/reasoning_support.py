"""Shared builders for the reasoning tests: hand-built annotation pieces and the water-law
sample."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from tests.test_annotation import LAW

Conn = psycopg.Connection[tuple[object, ...]]


WATER = Path(__file__).parent / "data" / "water_art7_art40.json"


WATER_DAY = date(2019, 1, 1)  # after the 2018-06-13 amendment, "並自公布日施行"


def _scope(concept: str, *, judgement: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {
        "role": "scope",
        "clause": 1,
        "concept": concept,
        "comparator": "eq",
        "threshold": 1,
    }
    if judgement:
        c["judgement"] = True
    return c


def _with(doc: dict[str, Any], *codes: str) -> dict[str, Any]:
    """The document plus concepts it mentions beyond the two the shared builder declares."""
    doc["concepts"] += [{"code": c, "label": c} for c in codes]
    return doc


def _plain_duty(text: str = "甲一", limit: int = 100) -> dict[str, Any]:
    """Art. 1: dischargers must keep COD at or under a literal limit."""
    return {
        "id": "duty",
        "law": LAW,
        "path": "art_1",
        "text": text,
        "kind": "prescriptive",
        "deontic": "obligation",
        "conditions": [
            _scope("discharging"),
            {
                "role": "duty",
                "clause": 1,
                "concept": "cod",
                "comparator": "le",
                "threshold": limit,
            },
        ],
    }
