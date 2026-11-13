"""`ListLaws` 與 `GetLawGraph` gRPC 契約及多層授權知識圖譜查詢測試（真實 PostgreSQL）。"""

from pathlib import Path
from typing import Any

import grpc
import psycopg
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from envcite.ontology.v2 import ontology_pb2, ontology_pb2_grpc
from envcite_ontology import errors
from envcite_ontology.acquire.moj import Moj
from envcite_ontology.delegation import extract as extract_delegations
from envcite_ontology.guidance import load as load_guidance
from envcite_ontology.ingest.importer import run as run_import
from envcite_ontology.ruling import load as load_rulings
from envcite_ontology.violation import extract as extract_violations

from .conftest import call_metadata, identifier
from .sample import stored_snapshot

Conn = psycopg.Connection[tuple[object, ...]]
Stub = ontology_pb2_grpc.OntologyServiceStub


def _moj_law(
    pcode: str,
    level: str,
    name: str,
    articles: list[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "LawLevel": level,
        "LawName": name,
        "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
        "LawModifiedDate": "20180801",
        "LawEffectiveDate": "",
        "LawEffectiveNote": "",
        "LawAbandonNote": "",
        "LawHistories": (
            "1.中華民國一百年一月一日總統令制定公布全文 10 條\r\n"
            "2.中華民國一百零七年八月一日修正發布全文 10 條；並自公布日施行"
        ),
        "LawArticles": [
            {"ArticleType": "A", "ArticleNo": f"第 {no} 條", "ArticleContent": text}
            for no, text in articles
        ],
    }


def _failure(error: grpc.RpcError) -> tuple[str, list[tuple[str, str]]]:
    status = rpc_status.from_call(error)  # type: ignore[arg-type]
    assert status is not None
    reason = ""
    violations: list[tuple[str, str]] = []
    for detail in status.details:
        if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
            info = error_details_pb2.ErrorInfo()
            detail.Unpack(info)
            reason = info.reason
        elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
            bad = error_details_pb2.BadRequest()
            detail.Unpack(bad)
            violations = [(v.field, v.reason) for v in bad.field_violations]
    return reason, violations


@pytest.fixture
def legal_graph_corpus(conn: Conn, tmp_path: Path) -> None:
    laws = [
        _moj_law(
            "A0000001",
            "憲法",
            "中華民國憲法",
            [("1", "中華民國基於三民主義，為民有民治民享之民主共和國。")],
        ),
        _moj_law(
            "G0340107",
            "法律",
            "規費法",
            [("10", "業務主管機關應依下列原則，訂定或調整收費基準。")],
        ),
        _moj_law(
            "O0020001",
            "法律",
            "空氣污染防制法",
            [
                ("1", "為防制空氣污染，維護國民健康，特制定本法。"),
                (
                    "20",
                    "公私場所固定污染源排放空氣污染物，應符合排放標準。\r\n"
                    "前項排放標準，由中央主管機關定之。",
                ),
                ("42", "本法授權由中央主管機關訂定施行細則與管理辦法。"),
                (
                    "62",
                    "公私場所違反第二十條第一項者，處新臺幣十萬元以上二千萬元以下罰鍰。"
                    "違反依第二十條第二項所定標準者，按次處罰。",
                ),
            ],
        ),
        _moj_law(
            "O0020002",
            "命令",
            "空氣污染防制法施行細則",
            [
                ("1", "本細則依空氣污染防制法第四十二條規定訂定之。"),
                ("5", "中央主管機關得依本細則訂定檢測作業準則。"),
            ],
        ),
        _moj_law(
            "O0020003",
            "命令",
            "固定污染源空氣污染物排放標準",
            [("1", "本標準依空氣污染防制法第二十條第二項規定訂定之。")],
        ),
        _moj_law(
            "O0020004",
            "命令",
            "公私場所固定污染源許可證管理辦法",
            [("1", "本辦法依空氣污染防制法第四十二條及規費法第十條規定訂定之。")],
        ),
        _moj_law(
            "O0020099",
            "命令",
            "空氣污染檢測作業準則",
            [("1", "本準則依空氣污染防制法施行細則第五條規定訂定之。")],
        ),
    ]
    stored_snapshot(conn, tmp_path, laws)
    run_import(conn, tmp_path, Moj())
    extract_delegations(conn)
    extract_violations(conn)
    load_guidance(
        conn,
        {
            "derivation": {"method": "human", "agent": "editor", "agent_version": "1"},
            "documents": [
                {
                    "issuer": "環境部",
                    "document_no": "環空字第100號",
                    "issued_on": "2021-03-01",
                    "kind": "interpretation",
                    "subject": "固定污染源連續測定設施操作規範釋疑\n說明：詳細操作附表略。",
                    "body": "全文如主旨。",
                    "source_url": "https://oaout.moenv.gov.tw/Law/100",
                    "interprets": [
                        {
                            "law": "O0020001",
                            "path": "art_20/para_1",
                            "text": "公私場所固定污染源排放空氣污染物，應符合排放標準。",
                        }
                    ],
                },
                {
                    "issuer": "環境部",
                    "document_no": "環空字第099號",
                    "issued_on": "2020-01-15",
                    "ceased_on": "2022-06-01",
                    "kind": "interpretation",
                    "subject": "已停止適用之舊函釋",
                    "body": "舊函釋內容。",
                    "interprets": [
                        {
                            "law": "O0020001",
                            "path": "art_20/para_1",
                            "text": "公私場所固定污染源排放空氣污染物，應符合排放標準。",
                        }
                    ],
                },
            ],
        },
    )
    load_rulings(
        conn,
        {
            "rulings": [
                {
                    "ruling_no": "釋字第777號",
                    "kind": "interpretation",
                    "decided_on": "2019-05-01",
                    "source_url": "https://cons.judicial.gov.tw/docdata.aspx?fid=100&id=777",
                    "excerpt": "第1條部分規定違憲。",
                    "invalidates": [
                        {
                            "law": "O0020001",
                            "path": "art_1",
                            "invalid_from": "2020-01-01",
                            "partial": True,
                        }
                    ],
                }
            ]
        },
    )
    conn.commit()


pytestmark = pytest.mark.usefixtures("legal_graph_corpus")


def test_list_laws_reports_document_type_counts_and_category_prefix(stub: Stub) -> None:
    env_resp = stub.ListLaws(
        ontology_pb2.ListLawsRequest(as_of="2023-01-01", category_prefix="O"),
        metadata=call_metadata(),
    )
    by_id = {item.identifier.local_id: item for item in env_resp.laws}

    assert set(by_id) == {"O0020001", "O0020002", "O0020003", "O0020004", "O0020099"}
    air = by_id["O0020001"]
    assert (air.document_type, air.child_count, air.parent_count, air.guidance_count) == (
        "法",
        3,
        0,
        1,
    )
    permit = by_id["O0020004"]
    assert (permit.document_type, permit.parent_count) == ("辦法", 2)
    assert permit.parent_identifier.local_id in {"O0020001", "G0340107"}

    early = stub.ListLaws(
        ontology_pb2.ListLawsRequest(as_of="2021-06-01", category_prefix="O"),
        metadata=call_metadata(),
    )
    early_air = next(x for x in early.laws if x.identifier.local_id == "O0020001")
    assert early_air.guidance_count == 2


def test_get_law_graph_focus_returns_multilayer_hierarchy_links_and_truncation(stub: Stub) -> None:
    resp = stub.GetLawGraph(
        ontology_pb2.GetLawGraphRequest(
            identifier=identifier("O0020001"),
            as_of="2023-01-01",
            downstream_depth=2,
            child_limit=2,
        ),
        metadata=call_metadata(),
    )

    assert resp.focus_identifier.local_id == "O0020001"
    nodes = {n.identifier.local_id: n for n in resp.nodes}
    assert nodes["O0020001"].hierarchy_tier == 1
    assert nodes["O0020001"].total_children == 3
    assert nodes["O0020001"].truncated_children == 1
    assert nodes["O0020001"].article_count == 4
    assert nodes["O0020001"].invalidation_count == 1
    assert nodes["O0020001"].guidance_count == 1
    assert nodes["O0020002"].hierarchy_tier == 2
    assert nodes["O0020099"].hierarchy_tier == 3

    assert {(cg.document_type, cg.count) for cg in resp.child_groups} == {
        ("細則", 1),
        ("標準", 1),
        ("辦法", 1),
    }

    edge_map = {(e.source.local_id, e.target.local_id): e for e in resp.edges}
    sub_edge = edge_map[("O0020002", "O0020099")]
    assert sub_edge.kind == ontology_pb2.GRAPH_EDGE_KIND_SUB_DELEGATION
    assert list(sub_edge.anchor_articles) == ["第 5 條"]
    assert sub_edge.all_rooted is True

    direct_edge = edge_map[("O0020001", "O0020003")]
    assert direct_edge.kind == ontology_pb2.GRAPH_EDGE_KIND_DELEGATION
    assert list(direct_edge.anchor_articles) == ["第 20 條第 2 項"]

    assert {(v.penalty_article, v.kind, v.target_article) for v in resp.violation_links} == {
        ("62", ontology_pb2.VIOLATION_REFERENCE_KIND_DUTY, "20"),
        ("62", ontology_pb2.VIOLATION_REFERENCE_KIND_DELEGATED_RULE, "20"),
    }

    (guidance_item,) = resp.guidance_links
    assert guidance_item.document_no == "環空字第100號"
    assert guidance_item.article_number == "20"
    assert "說明：" not in guidance_item.subject


def test_get_law_graph_supports_multiparent_upstream_and_document_type_filter(stub: Stub) -> None:
    permit_graph = stub.GetLawGraph(
        ontology_pb2.GetLawGraphRequest(identifier=identifier("O0020004"), as_of="2023-01-01"),
        metadata=call_metadata(),
    )
    permit_nodes = {n.identifier.local_id: n for n in permit_graph.nodes}
    assert {"O0020001", "G0340107", "O0020004"} <= set(permit_nodes)
    assert permit_nodes["O0020004"].hierarchy_tier == 2

    filtered = stub.GetLawGraph(
        ontology_pb2.GetLawGraphRequest(
            identifier=identifier("O0020001"),
            as_of="2023-01-01",
            child_document_type="辦法",
        ),
        metadata=call_metadata(),
    )
    filtered_ids = {n.identifier.local_id for n in filtered.nodes}
    assert filtered_ids == {"O0020001", "O0020004"}
    air_node = next(n for n in filtered.nodes if n.identifier.local_id == "O0020001")
    assert (air_node.total_children, air_node.truncated_children) == (3, 2)


def test_get_law_graph_overview_mode_and_category_prefix(stub: Stub) -> None:
    all_overview = stub.GetLawGraph(
        ontology_pb2.GetLawGraphRequest(as_of="2023-01-01"),
        metadata=call_metadata(),
    )
    assert not all_overview.HasField("focus_identifier")
    all_nodes = {n.identifier.local_id: n for n in all_overview.nodes}
    assert all_nodes["A0000001"].hierarchy_tier == 0
    assert "O0020001" in all_nodes

    env_overview = stub.GetLawGraph(
        ontology_pb2.GetLawGraphRequest(as_of="2023-01-01", category_prefix="O"),
        metadata=call_metadata(),
    )
    env_nodes = {n.identifier.local_id for n in env_overview.nodes}
    assert "A0000001" not in env_nodes
    assert {"O0020001", "O0020002", "O0020099"} <= env_nodes
    assert "G0340107" in env_nodes  # cross-category authorizing parent is preserved


def test_get_law_graph_and_list_laws_validate_arguments_and_not_found(stub: Stub) -> None:
    with pytest.raises(grpc.RpcError) as partial_id:
        stub.GetLawGraph(
            ontology_pb2.GetLawGraphRequest(
                identifier=ontology_pb2.LawIdentifier(local_id="O0020001")
            ),
            metadata=call_metadata(),
        )
    assert partial_id.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _failure(partial_id.value)[1] == [
        ("identifier.jurisdiction", errors.EMPTY),
        ("identifier.id_scheme", errors.EMPTY),
    ]

    with pytest.raises(grpc.RpcError) as bad_prefix:
        stub.ListLaws(
            ontology_pb2.ListLawsRequest(category_prefix="env"),
            metadata=call_metadata(),
        )
    assert bad_prefix.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _failure(bad_prefix.value)[1] == [("category_prefix", errors.UNKNOWN_VALUE)]

    with pytest.raises(grpc.RpcError) as bad_depth:
        stub.GetLawGraph(
            ontology_pb2.GetLawGraphRequest(
                identifier=identifier("O0020001"),
                upstream_depth=5,
                child_limit=99,
            ),
            metadata=call_metadata(),
        )
    assert bad_depth.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _failure(bad_depth.value)[1] == [
        ("upstream_depth", errors.OUT_OF_RANGE),
        ("child_limit", errors.OUT_OF_RANGE),
    ]

    with pytest.raises(grpc.RpcError) as missing_law:
        stub.GetLawGraph(
            ontology_pb2.GetLawGraphRequest(identifier=identifier("Z9999999")),
            metadata=call_metadata(),
        )
    assert missing_law.value.code() == grpc.StatusCode.NOT_FOUND
    assert _failure(missing_law.value)[0] == errors.LAW_NOT_FOUND

    with pytest.raises(grpc.RpcError) as uncovered_date:
        stub.GetLawGraph(
            ontology_pb2.GetLawGraphRequest(identifier=identifier("O0020001"), as_of="1990-01-01"),
            metadata=call_metadata(),
        )
    assert uncovered_date.value.code() == grpc.StatusCode.NOT_FOUND
    assert _failure(uncovered_date.value)[0] == errors.AS_OF_NOT_COVERED
