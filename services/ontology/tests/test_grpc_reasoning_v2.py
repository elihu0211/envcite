"""ReasoningService v2 gRPC 測試：複合自然鍵 LawIdentifier、錯誤模型與雙軌推論介面。"""

import grpc
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from envcite.ontology.v2 import ontology_pb2, reasoning_pb2, reasoning_pb2_grpc
from envcite_ontology import errors
from tests.conftest import call_metadata

StubV2 = reasoning_pb2_grpc.ReasoningServiceStub


def _details(error: grpc.RpcError) -> tuple[error_details_pb2.ErrorInfo, list[str]]:
    status = rpc_status.from_call(error)  # type: ignore[arg-type]
    assert status is not None
    info = error_details_pb2.ErrorInfo()
    fields: list[str] = []
    for detail in status.details:
        if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
            detail.Unpack(info)
        elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
            bad = error_details_pb2.BadRequest()
            detail.Unpack(bad)
            fields = [v.field for v in bad.field_violations]
    return info, fields


def test_v2_list_statements_requires_local_id(reasoning_stub_v2: StubV2) -> None:
    request = reasoning_pb2.ListStatementsRequest(
        identifier=ontology_pb2.LawIdentifier(jurisdiction="TW-TPE", id_scheme="local_glrs"),
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub_v2.ListStatements(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(err.value)
    assert info.reason == errors.INVALID_REQUEST
    assert "identifier.local_id" in fields


def test_v2_list_statements_unknown_law_returns_not_found(reasoning_stub_v2: StubV2) -> None:
    request = reasoning_pb2.ListStatementsRequest(
        identifier=ontology_pb2.LawIdentifier(
            jurisdiction="TW-TPE", id_scheme="local_glrs", local_id="NON_EXISTENT_999"
        ),
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub_v2.ListStatements(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.NOT_FOUND
    info, _ = _details(err.value)
    assert info.reason == errors.LAW_NOT_FOUND


def test_v2_evaluate_reports_invalid_fields(reasoning_stub_v2: StubV2) -> None:
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="not-a-date",
        facts=[
            reasoning_pb2.Fact(concept="ap_intent", values=[reasoning_pb2.FactValue(flag=True)]),
            reasoning_pb2.Fact(
                concept="cod",
                values=[reasoning_pb2.FactValue(number=reasoning_pb2.Decimal(value="bad-num"))],
            ),
        ],
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub_v2.Evaluate(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(err.value)
    assert info.reason == errors.INVALID_REQUEST
    assert "conduct_on" in fields
    assert "decided_on" in fields
    assert "facts[0].concept" in fields
    assert "facts[1].values[0].number.value" in fields


def test_v2_evaluate_date_order_violation(reasoning_stub_v2: StubV2) -> None:
    request = reasoning_pb2.EvaluateRequest(
        conduct_on="2025-06-01",
        decided_on="2025-01-01",
    )
    with pytest.raises(grpc.RpcError) as err:
        reasoning_stub_v2.Evaluate(request, metadata=call_metadata())

    assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    info, fields = _details(err.value)
    assert info.reason == errors.INVALID_REQUEST
    assert "conduct_on" in fields


def test_v2_codec_dual_track_delegation_and_uncovered() -> None:
    """測試推論編碼器原生支援中央與地方雙軌授權鏈與未涵蓋法規識別碼。"""
    from envcite_ontology import reasoning_codec as codec
    from envcite_ontology.reasoning import Result
    from envcite_ontology.reasoning.facts import Chain, Link

    central_act = Link(
        command_law_id="TPE-ENV-001",
        cited_law_name="空氣污染防制法",
        cited_path="art_20",
        law_id="O0020001",
        version_iri="https://envcite/tw/law/O0020001/2018-08-01/zho/art_20",
        gap=None,
        command_jurisdiction="TW-TPE",
        command_id_scheme="local_glrs",
        target_jurisdiction="TW-CENTRAL",
        target_id_scheme="moj_pcode",
    )
    chain = Chain(rooted=True, links=(central_act,))
    basis = codec._delegation_v2(statement_id=101, chain=chain)

    assert basis.statement_id == 101
    assert basis.rooted is True
    assert len(basis.links) == 1
    link = basis.links[0]
    assert link.command_identifier.jurisdiction == "TW-TPE"
    assert link.command_identifier.id_scheme == "local_glrs"
    assert link.command_identifier.local_id == "TPE-ENV-001"
    assert link.identifier.jurisdiction == "TW-CENTRAL"
    assert link.identifier.id_scheme == "moj_pcode"
    assert link.identifier.local_id == "O0020001"
    assert link.cited_law_name == "空氣污染防制法"

    # 驗證 outcome_v2 原生封裝 uncovered_law_identifiers
    from envcite_ontology.reasoning.engine import General

    general = General(unpunished=(), mitigation=(), presumed=(), time_barred=())
    result = Result(
        verdicts={101: "violation"},
        penalties=(),
        one_act=None,
        missing=(),
        needs_judgement=(),
        judgement_used=(),
        problems=(),
        unresolved_conflicts=(),
        defeated=(),
        unresolved_penalty_overlaps=(),
        penalty_defeated=(),
        trace=(),
        limit_sources={},
        general=general,
        delegation={101: chain},
    )
    uncovered = (
        ("TW-CENTRAL", "moj_pcode", "O0020001"),
        ("TW-TPE", "local_glrs", "TPE-ENV-001"),
    )
    out = codec.outcome_v2(result, sources={101: "https://iri"}, uncovered=uncovered)
    assert len(out.uncovered_law_identifiers) == 2
    assert out.uncovered_law_identifiers[0].jurisdiction == "TW-CENTRAL"
    assert out.uncovered_law_identifiers[0].local_id == "O0020001"
    assert out.uncovered_law_identifiers[1].jurisdiction == "TW-TPE"
    assert out.uncovered_law_identifiers[1].local_id == "TPE-ENV-001"
    assert len(out.delegation_bases) == 1
    assert out.delegation_bases[0].links[0].command_identifier.jurisdiction == "TW-TPE"
    assert out.delegation_bases[0].links[0].identifier.jurisdiction == "TW-CENTRAL"
