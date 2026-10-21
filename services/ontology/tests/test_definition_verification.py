"""A definition becomes a fact for inference only after two independent methods agree: the words are
in the text, and a model of another family, reading only the text, finds the same term and members.
Either failing rejects it; a missing reader confirms nothing."""

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from envcite_ontology.annotate import load
from envcite_ontology.reasoning import evaluate
from envcite_ontology.verification import definition_gates as dg
from envcite_ontology.verification.definitions import extract_definitions
from tests.model_gates_support import Scripted
from tests.test_annotation import BEFORE, LAW, _doc
from tests.test_definitions_extraction import ARTICLE_2, ITEM_1, PATH_1
from tests.test_import import _scalar
from tests.test_incremental import _articles, _first, _law_v1
from tests.test_reasoning import _belonging_rule
from tests.verification_support import Conn, FakeModel

EXTRACTOR = "alpha:extractor"
READER_OK = {"term": "事業", "members": ["工廠", "礦場"]}


def _extracted(conn: Conn, tmp_path: Path, members: list[str] | None = None) -> int:
    """A law with a definition clause and one definition extracted by `alpha:extractor`."""
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))
    labels = members or ["工廠", "礦場"]
    answer = {
        "definitions": [
            {
                "quote": ITEM_1,
                "term": "事業",
                "code": "regulated",
                "members": [{"label": m, "code": f"m{i}"} for i, m in enumerate(labels)],
            }
        ]
    }
    extract_definitions(conn, FakeModel(answer), LAW, PATH_1, agent=EXTRACTOR, agent_version="1")
    return int(_scalar(conn, "SELECT id FROM annotation.term_definition"))


def _state(conn: Conn) -> str:
    return str(_scalar(conn, "SELECT confirm_state::text FROM annotation.assertion"))


def test_the_literal_gate_passes_words_that_are_in_the_text(conn: Conn, tmp_path: Path) -> None:
    did = _extracted(conn, tmp_path)

    assert dg.LiteralGate().check(conn, did).verdict == "pass"


def test_the_literal_gate_fails_a_member_the_text_does_not_have(conn: Conn, tmp_path: Path) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))
    doc = _doc(review="extracted")
    doc["scheme"] = {"code": f"def.{LAW}", "title": "本法用語", "law": LAW}
    doc["concepts"] = [{"code": "t", "label": "事業"}, {"code": "s", "label": "學校"}]
    doc["definitions"] = [
        {"id": "d", "law": LAW, "path": PATH_1, "text": ITEM_1, "term": "t",
         "definition": ITEM_1, "members": ["s"]}
    ]  # fmt: skip
    load(conn, doc)
    did = int(_scalar(conn, "SELECT id FROM annotation.term_definition"))

    result = dg.LiteralGate().check(conn, did)

    assert result.verdict == "fail"
    assert "學校" in str(result.evidence["problems"])


def test_the_roundtrip_gate_compares_term_and_members_in_both_directions(
    conn: Conn, tmp_path: Path
) -> None:
    did = _extracted(conn, tmp_path)

    same = dg.DefinitionRoundtripGate(Scripted("beta:reader", READER_OK)).check(conn, did)
    missed = dg.DefinitionRoundtripGate(
        Scripted("beta:reader", {"term": "事業", "members": ["工廠"]})
    ).check(conn, did)
    extra = dg.DefinitionRoundtripGate(
        Scripted("beta:reader", {"term": "事業", "members": ["工廠", "礦場", "學校"]})
    ).check(conn, did)

    assert same.verdict == "pass"
    assert missed.verdict == "fail" and extra.verdict == "fail"


def test_the_roundtrip_gate_does_not_count_the_extractors_own_family(
    conn: Conn, tmp_path: Path
) -> None:
    did = _extracted(conn, tmp_path)

    result = dg.DefinitionRoundtripGate(Scripted("alpha:other", READER_OK)).check(conn, did)

    assert result.verdict == "inconclusive"


@pytest.mark.parametrize("reply", ["not json", {"term": "事業", "members": "工廠"}])
def test_an_unreadable_reread_is_inconclusive_not_a_failure(
    conn: Conn, tmp_path: Path, reply: Any
) -> None:
    did = _extracted(conn, tmp_path)

    result = dg.DefinitionRoundtripGate(Scripted("beta:reader", reply)).check(conn, did)

    assert result.verdict == "inconclusive"


def test_the_extractor_cannot_verify_its_own_definition(conn: Conn, tmp_path: Path) -> None:
    did = _extracted(conn, tmp_path)

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "SELECT annotation.record_verification(%s, 'literal', %s, 'pass')",
            (did, EXTRACTOR),
        )


def test_both_methods_passing_confirms_and_the_definition_enters_inference(
    conn: Conn, tmp_path: Path
) -> None:
    _first(conn, tmp_path, _law_v1(LawArticles=_articles("甲一", ARTICLE_2)))
    rule = _doc(_belonging_rule())
    rule["concepts"] = [
        {"code": "discharging", "label": "排放廢水"},
        {"code": "industry", "label": "行業"},
        {"code": "regulated", "label": "受管制事業"},
    ]
    duty = load(conn, rule).ids["duty"]
    answer = {
        "definitions": [
            {
                "quote": ITEM_1,
                "term": "事業",
                "code": "regulated",
                "members": [
                    {"label": "工廠", "code": "factory"},
                    {"label": "礦場", "code": "mine"},
                ],
            }
        ]
    }
    extract_definitions(conn, FakeModel(answer), LAW, PATH_1, agent=EXTRACTOR, agent_version="1")
    case: dict[str, float | str] = {"discharging": 1, "industry": "factory"}
    assert evaluate(conn, BEFORE, case).verdicts == {duty: "violation"}  # 未確認，不採計

    settled = dg.settle(conn, dg.gates_for(Scripted("beta:reader", READER_OK)))

    assert (settled.confirmed, settled.rejected) == (1, 0)
    assert _state(conn) == "confirmed"
    assert evaluate(conn, BEFORE, case).verdicts == {duty: "compliant"}


def test_a_failing_method_rejects_and_the_definition_never_enters_inference(
    conn: Conn, tmp_path: Path
) -> None:
    _extracted(conn, tmp_path)

    settled = dg.settle(
        conn, dg.gates_for(Scripted("beta:reader", {"term": "事業", "members": ["工廠"]}))
    )

    assert (settled.confirmed, settled.rejected) == (0, 1)
    assert _state(conn) == "rejected"
    assert (
        _scalar(
            conn,
            "SELECT count(*) FROM annotation.definitions_at(%s, 'zho', %s)",
            _scalar(conn, "SELECT id FROM corpus.legal_resource"),
            date(2019, 1, 1),
        )
        == 0
    )


def test_without_a_reader_a_definition_is_not_confirmed(conn: Conn, tmp_path: Path) -> None:
    """只有確定性的字面檢查不夠：沒有另一家族的重讀者，什麼都不確認。"""
    _extracted(conn, tmp_path)

    settled = dg.settle(conn, dg.gates_for(None))

    assert (settled.confirmed, settled.rejected) == (0, 0)
    assert _state(conn) == "extracted"


def test_an_inconclusive_reread_is_retried_next_time(conn: Conn, tmp_path: Path) -> None:
    _extracted(conn, tmp_path)
    dg.settle(conn, dg.gates_for(Scripted("beta:reader", "not json")))
    assert _state(conn) == "extracted"

    settled = dg.settle(conn, dg.gates_for(Scripted("beta:reader", READER_OK)))

    assert settled.confirmed == 1 and _state(conn) == "confirmed"
