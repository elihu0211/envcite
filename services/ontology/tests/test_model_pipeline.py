"""The whole line with no one on it: extract, verify through every gate, accept."""

import json
from pathlib import Path

from env_law_ontology.reasoning import evaluate
from env_law_ontology.verification.run import (
    ALL_METHODS,
    run,
    units_of,
)
from tests.model_gates_support import (
    CASES,
    EXTRACTOR,
    FAITHFUL,
    SAME_AS_ANNOTATION,
    TEXT,
    Conn,
    Scripted,
)
from tests.test_annotation import BEFORE, LAW
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.verification_support import EXTRACTED, _review


def _by_role() -> list[Scripted]:
    """verifiers[0] reads back, verifiers[-1] writes tests, all of them sit on the panel."""

    def reply(prompt: str) -> str:
        if "結構化描述" in prompt and "法規測試設計者" not in prompt and "審查者" not in prompt:
            return json.dumps(SAME_AS_ANNOTATION)
        if "法規測試設計者" in prompt:
            return json.dumps(CASES)
        return json.dumps(FAITHFUL)

    return [Scripted("fake-b:v", reply), Scripted("fake-c:v", reply)]


def test_units_of_lists_the_units_that_have_text(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))

    assert units_of(conn, LAW) == ["art_1", "art_2"]
    assert units_of(conn, "NOPE") == []


def test_from_text_to_conclusion_through_every_gate_without_a_person(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    extractor = Scripted(EXTRACTOR, EXTRACTED)

    result = run(conn, extractor, _by_role(), [LAW], agent_version="1")

    assert (result.units, result.skipped, result.extracted) == (2, 0, 1)
    assert result.dropped == 3  # 兩個單元裡，原文沒有的引文各被丟掉
    assert (result.settled.confirmed, result.settled.rejected) == (1, 0)
    verdicts = evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0}).verdicts
    assert list(verdicts.values()) == ["violation"]
    methods = {
        str(m) for (m,) in conn.execute("SELECT DISTINCT method FROM annotation.verification")
    }
    assert set(ALL_METHODS) <= methods | {"policy"}


def test_a_second_run_does_not_extract_the_same_unit_again(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    extractor = Scripted(EXTRACTOR, EXTRACTED)
    run(conn, extractor, _by_role(), [LAW], agent_version="1")
    calls = len(extractor.prompts)

    again = run(conn, extractor, _by_role(), [LAW], agent_version="1")

    assert again.skipped == 2  # 沒抽出東西的 art_2 也有抽取紀錄，不再問第二次
    assert len(extractor.prompts) == calls
    assert _scalar(conn, "SELECT count(*) FROM annotation.statement") == 1


def test_verifiers_of_the_extractors_family_accept_nothing_unless_explicitly_allowed(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    cousins = [Scripted("fake-a:v1", SAME_AS_ANNOTATION), Scripted("fake-a:v2", CASES)]

    strict = run(conn, Scripted(EXTRACTOR, EXTRACTED), cousins, [LAW], agent_version="1")
    assert strict.settled.confirmed == 0
    sid = int(_scalar(conn, "SELECT id FROM annotation.statement"))
    assert _review(conn, sid) == "extracted"  # 同家族的共同偏誤擋不住，所以不接受
    assert evaluate(conn, BEFORE, {"discharging": 1, "compliant": 0}).verdicts == {}


def test_a_failed_model_gate_rejects_even_when_the_deterministic_ones_pass(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles(TEXT, "乙一")))
    verifiers = [
        Scripted("fake-b:reader", {**SAME_AS_ANNOTATION, "deontic": "permission"}),
        Scripted("fake-c:tester", CASES),
    ]

    result = run(conn, Scripted(EXTRACTOR, EXTRACTED), verifiers, [LAW], agent_version="1")

    assert (result.settled.confirmed, result.settled.rejected) == (0, 1)
