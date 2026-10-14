"""The description every verifier reads, and the round-trip gate."""

import json
from pathlib import Path
from typing import Any

import pytest

from envcite_ontology.verification import (
    RoundtripGate,
)
from envcite_ontology.verification.extract import extract_unit
from envcite_ontology.verification.model_gates import REFORMALIZE, render_annotation
from tests.model_gates_support import (
    EXTRACTOR,
    SAME_AS_ANNOTATION,
    TEXT,
    Conn,
    Scripted,
    _extracted,
)
from tests.test_annotation import LAW
from tests.test_incremental import _articles, _first, _law_v1


def test_the_description_is_deterministic_and_carries_the_concept_codes(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)

    text = render_annotation(conn, sid)

    assert text == render_annotation(conn, sid)
    assert "規範性陳述・義務" in text
    assert "[discharging] 排放廢水 成立" in text and "[compliant] 符合標準 成立" in text


def test_the_roundtrip_passes_when_another_reader_rebuilds_the_same_annotation(
    conn: Conn, tmp_path: Path
) -> None:
    sid = _extracted(conn, tmp_path)
    reader = Scripted("fake-b:reader", SAME_AS_ANNOTATION)

    result = RoundtripGate(reader).check(conn, sid)

    assert result is not None and result.verdict == "pass"
    assert TEXT not in reader.prompts[0]  # 它看不到原文


def test_the_roundtrip_fails_and_names_what_differs(conn: Conn, tmp_path: Path) -> None:
    sid = _extracted(conn, tmp_path)
    broken = json.loads(json.dumps(SAME_AS_ANNOTATION))
    broken["conditions"][1]["comparator"] = "ne"
    broken["deontic"] = "permission"

    result = RoundtripGate(Scripted("fake-b:reader", broken)).check(conn, sid)

    assert result is not None and result.verdict == "fail"
    assert set(result.evidence["differences"]) == {"deontic", "conditions"}


def test_a_direction_the_reader_adds_to_a_constitutive_statement_is_ignored(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    answer = {"concepts": [], "statements": [{"id": "d", "quote": TEXT, "kind": "constitutive"}]}
    out = extract_unit(conn, Scripted(EXTRACTOR, answer), LAW, "art_1", agent_version="1")
    assert out.loaded is not None
    reads_authority_as_permission = {
        "kind": "constitutive",
        "deontic": "permission",
        "conditions": [],
    }

    result = RoundtripGate(Scripted("fake-b:reader", reads_authority_as_permission)).check(
        conn, out.loaded.ids["d"]
    )

    assert result is not None and result.verdict == "pass"


def test_the_reader_is_told_only_prescriptive_statements_have_a_direction() -> None:
    assert "只有 kind 為 prescriptive 才有 deontic" in REFORMALIZE


@pytest.mark.parametrize("answer", ["not json", {"kind": None}])
def test_an_unusable_roundtrip_answer_is_not_a_pass(
    conn: Conn, tmp_path: Path, answer: Any
) -> None:
    sid = _extracted(conn, tmp_path)

    result = RoundtripGate(Scripted("fake-b:reader", answer)).check(conn, sid)

    assert result is not None and result.verdict in {"inconclusive", "fail"}


def test_a_verifier_from_the_extractors_family_does_not_count(conn: Conn, tmp_path: Path) -> None:
    sid = _extracted(conn, tmp_path)
    cousin = Scripted("fake-a:reader", SAME_AS_ANNOTATION)

    refused = RoundtripGate(cousin).check(conn, sid)
    allowed = RoundtripGate(cousin, allow_same_family=True).check(conn, sid)

    assert refused is not None and refused.verdict == "inconclusive"
    assert refused.evidence["skipped"] == ["fake-a:reader"]
    assert cousin.prompts and allowed is not None and allowed.verdict == "pass"  # 明說才採計
    # 抽取者自己永遠不行，不論允不允許同家族。
    twin = RoundtripGate(Scripted(EXTRACTOR, SAME_AS_ANNOTATION), allow_same_family=True).check(
        conn, sid
    )
    assert twin is not None and twin.verdict == "inconclusive"
