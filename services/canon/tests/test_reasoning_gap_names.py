"""缺口帶著概念的名稱：欠缺的事實對人要說得出名字，不只是代碼。"""

from pathlib import Path

from canon import annotation_read
from canon.db import Conn
from canon.reasoning_codec import outcome as outcome_codec
from envcite.canon.v1 import reasoning_pb2
from tests.conftest import call_metadata
from tests.test_grpc_reasoning import Stub, _case, _committed


def test_a_missing_fact_comes_back_with_the_name_of_its_concept(
    conn: Conn, tmp_path: Path, reasoning_stub: Stub
) -> None:
    duty_id, _ = _committed(conn, tmp_path)
    only_scope = _case()
    only_scope.ClearField("facts")
    only_scope.facts.append(
        reasoning_pb2.Fact(concept="discharging", values=[reasoning_pb2.FactValue(flag=True)])
    )

    response = reasoning_stub.Evaluate(only_scope, metadata=call_metadata())

    gaps = [(g.statement_id, g.concept, g.name) for g in response.at_decision.missing]
    assert gaps == [(duty_id, "cod", "化學需氧量")]


def test_names_come_from_the_requirements_of_the_statements_and_only_for_known_codes(
    conn: Conn, tmp_path: Path
) -> None:
    duty_id, _ = _committed(conn, tmp_path)

    names = annotation_read.concept_names(
        conn, {duty_id}, {"cod", "discharging", "liability_basis"}
    )

    assert names == {"cod": "化學需氧量", "discharging": "排放廢水"}
    assert annotation_read.concept_names(conn, set(), {"cod"}) == {}
    assert annotation_read.concept_names(conn, {duty_id}, set()) == {}


def test_a_gap_whose_name_is_unknown_has_an_empty_name_not_a_guess() -> None:
    gaps = outcome_codec._gaps([(7, "cod"), (7, "liability_basis")], {"cod": "化學需氧量"})

    assert [(g.concept, g.name) for g in gaps] == [("cod", "化學需氧量"), ("liability_basis", "")]
